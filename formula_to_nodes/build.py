# SPDX-License-Identifier: GPL-3.0-or-later
"""Turns compiler IR into a real Geometry Nodes tree.

Rebuilding an existing group keeps its name, its interface socket
identifiers (so modifier values and links to the group node survive) and
every other user of the group.
"""

import bpy

from .compiler import Out
from . import layout

SOURCE_KEY = "ftn_source"
MODE_KEY = "ftn_mode"
OUTPUT_KEY = "ftn_output"
VERSION_KEY = "ftn_version"


class BuildError(Exception):
    pass


def is_formula_group(tree):
    return tree is not None and getattr(tree, "bl_idname", "") == "GeometryNodeTree" and SOURCE_KEY in tree


# ─────────────────────────────────────────────────────────────────────────────
#  Interface
# ─────────────────────────────────────────────────────────────────────────────

def _sockets(iface):
    return [it for it in iface.items_tree if it.item_type == "SOCKET"]


def _by_identifier(iface, ident):
    for it in iface.items_tree:
        if it.item_type == "SOCKET" and it.identifier == ident:
            return it
    return None


def sync_interface(tree, specs):
    """Make the tree's interface match ``specs`` (compiler IfaceSocket list),
    reusing sockets that have the same direction, name and type."""
    iface = tree.interface
    matched = {}
    used = set()
    existing = [(it.identifier, it.in_out, it.name, it.socket_type) for it in _sockets(iface)]
    for spec in specs:
        for ident, in_out, name, stype in existing:
            if ident in used:
                continue
            if in_out == spec.in_out and name == spec.name and stype == spec.socket_type:
                matched[spec.key] = ident
                used.add(ident)
                break
    for ident, *_ in existing:
        if ident not in used:
            it = _by_identifier(iface, ident)
            if it is not None:
                iface.remove(it)
    created = set()
    for spec in specs:
        if spec.key not in matched:
            it = iface.new_socket(spec.name, in_out=spec.in_out, socket_type=spec.socket_type)
            matched[spec.key] = it.identifier
            created.add(spec.key)

    ordered = [s for s in specs if s.in_out == "OUTPUT"] + [s for s in specs if s.in_out == "INPUT"]
    for pos, spec in enumerate(ordered):
        it = _by_identifier(iface, matched[spec.key])
        if it is not None and it.position != pos:
            iface.move(it, pos)

    for spec in specs:
        if spec.vtype == "GEOMETRY" or spec.in_out != "INPUT":
            continue
        it = _by_identifier(iface, matched[spec.key])
        if spec.vtype in ("FLOAT", "INT"):
            lo = -1.0e9 if spec.min is None else spec.min
            hi = 1.0e9 if spec.max is None else spec.max
            if spec.vtype == "INT":
                lo, hi = int(max(lo, -2**31)), int(min(hi, 2**31 - 1))
            it.min_value, it.max_value = lo, hi
        if spec.default is not None:
            it.default_value = spec.default
    _push_new_defaults(tree, [s for s in specs if s.key in created], matched)
    return matched


def _push_new_defaults(tree, new_specs, matched):
    """Blender snapshots a socket's value into existing modifiers and group
    nodes the moment the socket is created — before its default can be set.
    Give every existing user the real default for brand-new parameters."""
    specs = [s for s in new_specs if s.in_out == "INPUT" and s.vtype != "GEOMETRY" and s.default is not None]
    if not specs:
        return
    for ob in bpy.data.objects:
        for mod in ob.modifiers:
            if mod.type != "NODES" or mod.node_group != tree:
                continue
            inputs = getattr(getattr(mod, "properties", None), "inputs", None)
            for spec in specs:
                item = getattr(inputs, matched[spec.key], None) if inputs is not None else None
                if item is not None and hasattr(item, "value"):
                    try:
                        item.value = spec.default
                    except (TypeError, ValueError):
                        pass
    for group in bpy.data.node_groups:
        for node in group.nodes:
            if getattr(node, "node_tree", None) != tree:
                continue
            for spec in specs:
                sock = next((x for x in node.inputs if x.identifier == matched[spec.key]), None)
                if sock is not None and hasattr(sock, "default_value"):
                    try:
                        sock.default_value = spec.default
                    except (TypeError, ValueError):
                        pass


# ─────────────────────────────────────────────────────────────────────────────
#  Socket lookup
# ─────────────────────────────────────────────────────────────────────────────

def _find(sockets, key):
    for s in sockets:
        if s.identifier == key and not s.is_unavailable:
            return s
    for s in sockets:
        if s.identifier == key:
            return s
    for s in sockets:
        if s.name == key and not s.is_unavailable:
            return s
    return None


def _item_sockets(sockets):
    return [s for s in sockets
            if s.identifier not in ("Geometry", "Selection") and not s.identifier.startswith("__extend__")]


def _socket(bnode, key, ids, outputs):
    sockets = bnode.outputs if outputs else bnode.inputs
    if key.startswith(("param:", "out:")):
        sock = _find(sockets, ids.get(key, key))
    elif key.startswith("item:"):
        items = _item_sockets(sockets)
        k = int(key[5:])
        sock = items[k] if k < len(items) else None
    else:
        sock = _find(sockets, key)
    if sock is None:
        avail = ", ".join(s.identifier for s in sockets if not s.is_unavailable)
        side = "output" if outputs else "input"
        raise BuildError(f"internal: {bnode.bl_idname} has no {side} '{key}' (has: {avail})")
    return sock


# ─────────────────────────────────────────────────────────────────────────────
#  Materialize
# ─────────────────────────────────────────────────────────────────────────────

_OPTION_ROWS = {
    "ShaderNodeMath": 2, "ShaderNodeVectorMath": 1, "ShaderNodeMapRange": 3, "ShaderNodeMix": 3,
    "ShaderNodeClamp": 1, "FunctionNodeCompare": 2, "GeometryNodeSwitch": 1,
    "FunctionNodeBooleanMath": 1, "GeometryNodeInputNamedAttribute": 1,
    "GeometryNodeStoreNamedAttribute": 2, "GeometryNodeCaptureAttribute": 1,
    "FunctionNodeRandomValue": 1, "ShaderNodeVectorRotate": 2, "ShaderNodeTexNoise": 2,
    "GeometryNodeSampleIndex": 3, "GeometryNodeForeachGeometryElementOutput": 1,
    "GeometryNodeSetMeshNormal": 2, "ShaderNodeValue": 1, "FunctionNodeInputVector": 3,
    "FunctionNodeInputInt": 1, "FunctionNodeInputBool": 1, "GeometryNodeAttributeStatistic": 2,
    "NodeGroupInput": 0, "NodeGroupOutput": 0, "GeometryNodeInputPosition": 0,
    "GeometryNodeInputNormal": 1, "GeometryNodeInputIndex": 0, "GeometryNodeInputID": 0,
    "GeometryNodeCombineXYZ": 0, "ShaderNodeCombineXYZ": 0, "ShaderNodeSeparateXYZ": 0,
    "GeometryNodeSetPosition": 0, "GeometryNodeSetID": 0,
}


def _visible(s):
    return not (s.is_unavailable or s.hide or s.identifier.startswith("__extend__"))


def _estimate_height(b):
    h = 36.0
    h += 22.0 * sum(1 for s in b.outputs if _visible(s))
    for s in b.inputs:
        if not _visible(s):
            continue
        if s.type == "VECTOR" and not s.is_linked and not s.hide_value:
            h += 88.0
        else:
            h += 22.0
    h += 26.0 * _OPTION_ROWS.get(b.bl_idname, 1)
    return h


# Sockets with hidden values normally have an implicit field input (Set
# Position's Position = current position, Random Value's ID = index...).
# Blender ignores their default_value, so constants must be linked in from a
# node. These hidden-value sockets are the exception: they do honor defaults.
_HONORS_DEFAULT = {"Selection", "Attribute"}
_CONST_NODE = {
    "VALUE": ("ShaderNodeValue", None),
    "VECTOR": ("FunctionNodeInputVector", "vector"),
    "INT": ("FunctionNodeInputInt", "integer"),
    "BOOLEAN": ("FunctionNodeInputBool", "boolean"),
}


def _const_node(tree, cache, sock_type, value):
    key = (sock_type, value)
    node = cache.get(key)
    if node is None:
        idname, prop = _CONST_NODE[sock_type]
        node = tree.nodes.new(idname)
        if prop is None:
            node.outputs[0].default_value = value
        else:
            setattr(node, prop, value)
        cache[key] = node
    return node


def materialize(result, tree, do_layout=True):
    graph = result.graph
    ids = sync_interface(tree, result.iface)
    tree.nodes.clear()

    made = {}
    for n in graph.nodes:
        b = tree.nodes.new(n.idname)
        for k, v in n.props.items():
            try:
                setattr(b, k, v)
            except (TypeError, AttributeError, ValueError) as e:
                raise BuildError(f"internal: can't set {n.idname}.{k} = {v!r} ({e})") from None
        if n.label:
            b.label = n.label
        made[n.uid] = b

    for n in graph.nodes:
        b = made[n.uid]
        if n.pair is not None:
            b.pair_with_output(made[n.pair.uid])
        if n.items:
            for stype, name in n.items:
                b.capture_items.new(stype, name)
        if n.value is not None:
            b.outputs[0].default_value = n.value

    for n in graph.nodes:
        if n.idname == "NodeGroupInput":
            want = ids.get(n.visible)
            for s in made[n.uid].outputs:
                s.hide = s.identifier != want

    links = tree.links
    const_cache = {}
    extra = []
    for n in graph.nodes:
        b = made[n.uid]
        for key, v in n.inputs.items():
            sock = _socket(b, key, ids, outputs=False)
            if isinstance(v, Out):
                src = _socket(made[v.node.uid], v.sock, ids, outputs=True)
                links.new(src, sock)
            elif sock.hide_value and sock.identifier not in _HONORS_DEFAULT and sock.type in _CONST_NODE:
                cn = _const_node(tree, const_cache, sock.type, v)
                if cn not in extra:
                    extra.append(cn)
                links.new(cn.outputs[0], sock)
            else:
                try:
                    sock.default_value = v
                except (TypeError, ValueError) as e:
                    raise BuildError(f"internal: {n.idname}.{key} can't take {v!r} ({e})") from None

    if do_layout:
        every = list(made.values()) + extra
        boxes = {b.name: (float(b.width), _estimate_height(b)) for b in every}
        edges = [(l.from_node.name, l.to_node.name) for l in links]
        zones = [(made[n.uid].name, made[n.pair.uid].name) for n in graph.nodes if n.pair is not None]
        pos = layout.compute(boxes, edges, zones)
        for b in every:
            b.location = pos[b.name]
    return made


def build_group(result, source, mode, output="AUTO", name="Formula", target=None, version=""):
    """Create a new group (target=None) or rebuild ``target`` in place."""
    tree = target
    created = tree is None
    if created:
        tree = bpy.data.node_groups.new(name=name, type="GeometryNodeTree")
    try:
        materialize(result, tree)
    except Exception:
        if created:
            bpy.data.node_groups.remove(tree)
        raise
    tree[SOURCE_KEY] = source
    tree[MODE_KEY] = mode
    tree[OUTPUT_KEY] = output
    tree[VERSION_KEY] = version
    has_geo = any(s.key == "out:Geometry" for s in result.iface)
    if hasattr(tree, "is_modifier"):
        tree.is_modifier = has_geo
    if hasattr(tree, "description"):
        first = source.strip().splitlines()[0] if source.strip() else ""
        tree.description = f"Formula to Nodes: {first[:80]}"
    return tree
