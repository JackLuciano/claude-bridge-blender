"""nodes op: compact node-graph editing for materials, the world, Geometry Nodes and the compositor."""

import bpy

from .ops import _coerce, color, fmt, obj, op

ALIASES = {
    # shader
    "bsdf": "ShaderNodeBsdfPrincipled", "principled": "ShaderNodeBsdfPrincipled", "out": "ShaderNodeOutputMaterial",
    "emission": "ShaderNodeEmission", "glass": "ShaderNodeBsdfGlass", "transparent": "ShaderNodeBsdfTransparent",
    "mixshader": "ShaderNodeMixShader", "addshader": "ShaderNodeAddShader", "noise": "ShaderNodeTexNoise",
    "voronoi": "ShaderNodeTexVoronoi", "wave": "ShaderNodeTexWave", "gradient": "ShaderNodeTexGradient",
    "magic": "ShaderNodeTexMagic", "brick": "ShaderNodeTexBrick", "checker": "ShaderNodeTexChecker",
    "image": "ShaderNodeTexImage", "ramp": "ShaderNodeValToRGB", "colorramp": "ShaderNodeValToRGB",
    "mix": "ShaderNodeMix", "math": "ShaderNodeMath", "vmath": "ShaderNodeVectorMath", "maprange": "ShaderNodeMapRange",
    "bump": "ShaderNodeBump", "normalmap": "ShaderNodeNormalMap", "mapping": "ShaderNodeMapping",
    "coord": "ShaderNodeTexCoord", "texcoord": "ShaderNodeTexCoord", "geometry": "ShaderNodeNewGeometry",
    "layerweight": "ShaderNodeLayerWeight", "fresnel": "ShaderNodeFresnel", "ao": "ShaderNodeAmbientOcclusion",
    "hsv": "ShaderNodeHueSaturation", "invert": "ShaderNodeInvert", "rgb": "ShaderNodeRGB", "value": "ShaderNodeValue",
    "attribute": "ShaderNodeAttribute", "background": "ShaderNodeBackground", "sky": "ShaderNodeTexSky",
    "environment": "ShaderNodeTexEnvironment", "separate": "ShaderNodeSeparateXYZ", "combine": "ShaderNodeCombineXYZ",
    # geometry nodes
    "distribute": "GeometryNodeDistributePointsOnFaces", "instance": "GeometryNodeInstanceOnPoints",
    "objinfo": "GeometryNodeObjectInfo", "collinfo": "GeometryNodeCollectionInfo", "realize": "GeometryNodeRealizeInstances",
    "transform": "GeometryNodeTransform", "join": "GeometryNodeJoinGeometry", "setpos": "GeometryNodeSetPosition",
    "random": "FunctionNodeRandomValue", "position": "GeometryNodeInputPosition", "normal": "GeometryNodeInputNormal",
    "cube": "GeometryNodeMeshCube", "cylinder": "GeometryNodeMeshCylinder", "grid": "GeometryNodeMeshGrid",
    "ico": "GeometryNodeMeshIcoSphere", "uvsphere": "GeometryNodeMeshUVSphere", "line": "GeometryNodeMeshLine",
    "curvecircle": "GeometryNodeCurvePrimitiveCircle", "curvetomesh": "GeometryNodeCurveToMesh",
    "meshtopoints": "GeometryNodeMeshToPoints", "setmat": "GeometryNodeSetMaterial", "subdiv": "GeometryNodeSubdivisionSurface",
    "extrude": "GeometryNodeExtrudeMesh", "scale_el": "GeometryNodeScaleElements", "rotate_inst": "GeometryNodeRotateInstances",
    "scale_inst": "GeometryNodeScaleInstances", "align": "FunctionNodeAlignEulerToVector", "index": "GeometryNodeInputIndex",
    "compare": "FunctionNodeCompare", "switch": "GeometryNodeSwitch", "boolmath": "FunctionNodeBooleanMath",
    "delete": "GeometryNodeDeleteGeometry", "merge": "GeometryNodeMergeByDistance", "bounds": "GeometryNodeBoundBox",
    "raycast": "GeometryNodeRaycast", "sample": "GeometryNodeSampleNearestSurface",
    # compositor
    "glare": "CompositorNodeGlare", "blur": "CompositorNodeBlur", "lensdist": "CompositorNodeLensdist",
    "colorbalance": "CompositorNodeColorBalance", "curves": "CompositorNodeCurveRGB", "denoise": "CompositorNodeDenoise",
    "vignette_mask": "CompositorNodeEllipseMask", "composite": "CompositorNodeComposite", "viewer": "CompositorNodeViewer",
    "rlayers": "CompositorNodeRLayers", "brightcontrast": "CompositorNodeBrightContrast",
}
_PREFIXES = ("ShaderNode", "GeometryNode", "FunctionNode", "CompositorNode", "NodeGroup")


def _node_type(t):
    if t in ALIASES:
        return ALIASES[t]
    if hasattr(bpy.types, t):
        return t
    camel = "".join(p[:1].upper() + p[1:] for p in t.replace("-", "_").split("_"))
    for pre in _PREFIXES:
        if hasattr(bpy.types, pre + camel):
            return pre + camel
    raise ValueError(f"unknown node type '{t}' (use a Blender id like ShaderNodeTexNoise or an alias; api{{node}} lists sockets)")


def _new_gn_group(name):
    ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gi, go = ng.nodes.new("NodeGroupInput"), ng.nodes.new("NodeGroupOutput")
    gi.location, go.location = (-400, 0), (400, 0)
    ng.links.new(gi.outputs[0], go.inputs[0])
    return ng


def _compositor_tree(sc):
    if hasattr(sc, "compositing_node_group"):  # 5.x: the compositor is a node group
        if sc.compositing_node_group is None:
            ng = bpy.data.node_groups.new("Compositor", "CompositorNodeTree")
            sc.compositing_node_group = ng
            rl = ng.nodes.new("CompositorNodeRLayers")
            try:
                ng.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
                go = ng.nodes.new("NodeGroupOutput")
                ng.links.new(rl.outputs["Image"], go.inputs[0])
            except Exception:
                pass
        return sc.compositing_node_group
    sc.use_nodes = True
    return sc.node_tree


def resolve_tree(a):
    """(tree, label, default ids {id: node}) for mat|world|gn|comp."""
    if a.get("mat") or (a.get("obj") and not a.get("gn")):
        if a.get("mat"):
            m = bpy.data.materials.get(a["mat"]) or bpy.data.materials.new(a["mat"])
        else:
            o = obj(a["obj"])
            m = o.active_material
            if m is None:
                from .ops import op_mat
                op_mat({"obj": o.name})
                m = o.active_material
        if a.get("obj") and a.get("mat"):
            o = obj(a["obj"])
            if m.name not in [x.name for x in o.data.materials if x]:
                o.data.materials.append(m)
        m.use_nodes = True
        nt = m.node_tree
        ids = {"bsdf": next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None),
               "out": next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)}
        return nt, f"mat {m.name}", ids
    if a.get("world"):
        from .ops import _world_tree
        w, nt, out, bg = _world_tree()
        return nt, f"world {w.name}", {"out": out, "bg": bg}
    if a.get("gn"):
        ng = bpy.data.node_groups.get(a["gn"]) or _new_gn_group(a["gn"])
        if a.get("obj"):
            o = obj(a["obj"])
            md = next((m for m in o.modifiers if m.type == "NODES" and m.node_group == ng), None)
            if md is None:
                md = o.modifiers.new(ng.name, "NODES")
                md.node_group = ng
        ids = {"in": next((n for n in ng.nodes if n.type == "GROUP_INPUT"), None),
               "out": next((n for n in ng.nodes if n.type == "GROUP_OUTPUT"), None)}
        return ng, f"gn {ng.name}", ids
    if a.get("comp"):
        nt = _compositor_tree(bpy.context.scene)
        return nt, "compositor", {"rl": next((n for n in nt.nodes if n.type == "R_LAYERS"), None),
                                  "out": next((n for n in nt.nodes if n.type in ("COMPOSITE", "GROUP_OUTPUT")), None)}
    raise ValueError("nodes needs mat, obj, world, gn or comp")


def _find(nt, ids, key):
    if key in ids and ids[key] is not None:
        return ids[key]
    for n in nt.nodes:
        if n.get("cb_id") == key or n.name == key:
            return n
    raise ValueError(f"no node '{key}' (ids: {','.join(k for k in ids if ids[k]) or '-'}; "
                     f"nodes: {','.join(n.get('cb_id') or n.name for n in nt.nodes)[:200]})")


def _socket(node, name, outputs):
    socks = node.outputs if outputs else node.inputs
    if name is None:
        s = next((s for s in socks if s.enabled), None)
        if s is None:
            raise ValueError(f"{node.name} has no {'outputs' if outputs else 'inputs'}")
        return s
    if str(name).isdigit():
        return socks[int(name)]
    for s in socks:
        if s.enabled and (s.name == name or s.identifier == name):
            return s
    low = str(name).lower()
    for s in socks:
        if s.enabled and s.name.lower() == low:
            return s
    raise ValueError(f"{node.get('cb_id') or node.name} has no {'output' if outputs else 'input'} '{name}' "
                     f"(has: {','.join(s.name for s in socks if s.enabled)})")


def _set(nt, ids, key, v):
    """'n.Scale' = input default, 'n.noise_dimensions' = node property, 'r.0' / 'r.0.pos' = ColorRamp element."""
    nid, _, rest = key.partition(".")
    node = _find(nt, ids, nid)
    if node.type == "VALTORGB" and rest.split(".")[0].isdigit():
        i = int(rest.split(".")[0])
        els = node.color_ramp.elements
        while len(els) <= i:
            els.new(1.0)
        if rest.endswith(".pos"):
            els[i].position = float(v)
        else:
            els[i].color = color(v)
        return
    if hasattr(node, rest) and not any(s.name == rest for s in node.inputs):
        setattr(node, rest, _coerce(node, rest, v))
        return
    s = _socket(node, rest, False)
    if not hasattr(s, "default_value"):
        raise ValueError(f"input {rest} takes a link, not a value")
    if isinstance(v, str) and s.type in ("RGBA",):
        v = color(v)
    elif isinstance(v, str) and s.type in ("OBJECT", "MATERIAL", "COLLECTION", "IMAGE"):
        from .ops import _find_id
        v = _find_id(v, None)
    elif s.type == "RGBA" and isinstance(v, (list, tuple)):
        v = color(v)
    s.default_value = v


def _layout(nt):
    """Place nodes in columns by link depth so the graph stays readable in the editor."""
    depth = {n: 0 for n in nt.nodes}
    for _ in range(len(nt.nodes)):
        changed = False
        for l in nt.links:
            if depth[l.to_node] < depth[l.from_node] + 1:
                depth[l.to_node] = depth[l.from_node] + 1
                changed = True
        if not changed:
            break
    cols = {}
    for n, d in depth.items():
        cols.setdefault(d, []).append(n)
    for d, ns in cols.items():
        for i, n in enumerate(ns):
            n.location = (d * 260, -i * 220)


def _show(nt, ids):
    names = {n: n.get("cb_id") or n.name for n in nt.nodes}
    names.update({n: k for k, n in ids.items() if n is not None})
    nodes = " ".join(f"{names[n]}:{n.bl_idname}" for n in nt.nodes)
    links = " ".join(f"{names[l.from_node]}.{l.from_socket.name}>{names[l.to_node]}.{l.to_socket.name}" for l in nt.links)
    return f"nodes {nodes}\nlinks {links}"


@op("nodes")
def op_nodes(a):
    """Edit a node tree. Target: mat (material name; obj attaches it), obj (its active material), world,
    gn (geometry node group; obj adds the modifier), comp (compositor).
    add {id: type | {type, props}}: type = Blender id or alias (noise, ramp, mix, math, bump, image, distribute, instance...).
    set {"id.Socket": value, "id.prop": value, "ramp.0": color, "ramp.1.pos": 0.7}. link ["a.Out>b.In", "a>b.In"].
    del [ids]. show: list nodes and links. Pre-set ids: bsdf, out (materials); bg (world); in, out (gn); rl (comp).
    preset 'scatter' (gn + obj): instance (object to scatter), density, scale [min,max], seed."""
    nt, label, ids = resolve_tree(a)
    if a.get("preset") == "scatter":
        return _scatter(nt, ids, a, label)
    added = []
    for nid, spec in (a.get("add") or {}).items():
        spec = {"type": spec} if isinstance(spec, str) else spec
        old = next((n for n in nt.nodes if n.get("cb_id") == nid), None)
        if old is not None:
            nt.nodes.remove(old)
        n = nt.nodes.new(_node_type(spec["type"]))
        n["cb_id"] = nid
        ids[nid] = n
        for k, v in (spec.get("props") or {}).items():
            _set(nt, ids, f"{nid}.{k}", v)
        added.append(nid)
    for key, v in (a.get("set") or {}).items():
        _set(nt, ids, key, v)
    for spec in a.get("link") or []:
        src, _, dst = spec.partition(">")
        sn, _, ss = src.partition(".")
        dn, _, ds = dst.partition(".")
        out_s = _socket(_find(nt, ids, sn), ss or None, True)
        in_s = _socket(_find(nt, ids, dn), ds or None, False)
        nt.links.new(out_s, in_s)
    for nid in a.get("del") or []:
        nt.nodes.remove(_find(nt, ids, nid))
    if added or a.get("link"):
        _layout(nt)
    if a.get("show"):
        return _show(nt, ids)
    return f"{label}: {len(nt.nodes)} nodes, {len(nt.links)} links" + (f" (+{','.join(added)})" if added else "")


def _scatter(ng, ids, a, label):
    """Distribute Points on Faces -> Instance on Points (random scale/rotation) joined with the original."""
    if not a.get("instance"):
        raise ValueError("scatter needs instance (the object to scatter)")
    inst = obj(a["instance"])
    for n in [n for n in ng.nodes if n.get("cb_scatter")]:
        ng.nodes.remove(n)

    def node(t):
        n = ng.nodes.new(t)
        n["cb_scatter"] = True
        return n
    gi, go = ids["in"], ids["out"]
    dist, info, inst_n = node("GeometryNodeDistributePointsOnFaces"), node("GeometryNodeObjectInfo"), node("GeometryNodeInstanceOnPoints")
    rnd_s, rnd_r, join = node("FunctionNodeRandomValue"), node("FunctionNodeRandomValue"), node("GeometryNodeJoinGeometry")
    dist.distribute_method = "POISSON"
    dist.inputs["Density Max"].default_value = float(a.get("density", 5))
    if "Distance Min" in dist.inputs:
        dist.inputs["Distance Min"].default_value = float(a.get("spacing", 0.1))
    if "Seed" in dist.inputs:
        dist.inputs["Seed"].default_value = int(a.get("seed", 0))
    info.inputs["Object"].default_value = inst
    info.transform_space = "RELATIVE"
    smin, smax = a.get("scale", [0.7, 1.3])
    # Random Value has one Min/Max/Value socket per data type: pick the enabled one.
    rnd_s.data_type = "FLOAT"
    _socket(rnd_s, "Min", False).default_value = smin
    _socket(rnd_s, "Max", False).default_value = smax
    rnd_r.data_type = "FLOAT_VECTOR"
    _socket(rnd_r, "Min", False).default_value = (0, 0, 0)
    _socket(rnd_r, "Max", False).default_value = (0, 0, 6.2832)
    for l in [l for l in ng.links if l.to_node == go]:
        ng.links.remove(l)
    ng.links.new(gi.outputs[0], dist.inputs["Mesh"])
    ng.links.new(dist.outputs["Points"], inst_n.inputs["Points"])
    ng.links.new(info.outputs["Geometry"], inst_n.inputs["Instance"])
    ng.links.new(_socket(rnd_s, "Value", True), inst_n.inputs["Scale"])
    ng.links.new(_socket(rnd_r, "Value", True), inst_n.inputs["Rotation"])
    ng.links.new(gi.outputs[0], join.inputs[0])
    ng.links.new(inst_n.outputs["Instances"], join.inputs[0])
    ng.links.new(join.outputs[0], go.inputs[0])
    _layout(ng)
    return f"{label}: scatter {inst.name} density {fmt(float(a.get('density', 5)))}/m²"
