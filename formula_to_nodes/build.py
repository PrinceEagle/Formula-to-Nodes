# SPDX-License-Identifier: GPL-3.0-or-later
"""Turns compiler IR into a real Geometry Nodes tree.

Rebuilding an existing group keeps its name, its interface socket
identifiers (so modifier values and links to the group node survive), every
other user of the group, and the shapes of ramps (chramp) the user edited.
"""

import bpy

from .core import Out, quat_to_euler
from . import layout, ramps

SOURCE_KEY = "ftn_source"
MODE_KEY = "ftn_mode"
OUTPUT_KEY = "ftn_output"
VERSION_KEY = "ftn_version"
RUNOVER_KEY = "ftn_runover"
RAMP_KEY = "ftn_ramp"


class BuildError(Exception):
    pass


def is_formula_group(tree):
    return tree is not None and getattr(tree, "bl_idname", "") == "GeometryNodeTree" and SOURCE_KEY in tree


# ─────────────────────────────────────────────────────────────────────────────
#  Interface
# ─────────────────────────────────────────────────────────────────────────────

def _sockets(iface):
    return [it for it in iface.items_tree if it.item_type == "SOCKET"]


def _panels(iface):
    return [it for it in iface.items_tree if it.item_type == "PANEL"]


def _by_identifier(iface, ident):
    for it in iface.items_tree:
        if it.item_type == "SOCKET" and it.identifier == ident:
            return it
    return None


def _panel_name(it):
    p = it.parent
    return p.name if p is not None and p.name else None


def _root(iface):
    for it in iface.items_tree:
        if it.parent is not None and not it.parent.name and it.parent.item_type == "PANEL":
            return it.parent
    return None


def sync_interface(tree, specs):
    """Make the tree's interface match ``specs`` (compiler IfaceSocket list),
    reusing sockets that have the same direction, name and type. Parameters
    named "Folder/name" go into a panel called Folder."""
    iface = tree.interface
    matched = {}
    used = set()
    existing = [(it.identifier, it.in_out, it.name, it.socket_type, _panel_name(it)) for it in _sockets(iface)]
    for prefer_panel in (True, False):
        for spec in specs:
            if spec.key in matched:
                continue
            for ident, in_out, name, stype, panel in existing:
                if ident in used:
                    continue
                if in_out == spec.in_out and name == spec.name and stype == spec.socket_type \
                        and (not prefer_panel or panel == spec.panel):
                    matched[spec.key] = ident
                    used.add(ident)
                    break
    for ident, *_ in existing:
        if ident not in used:
            it = _by_identifier(iface, ident)
            if it is not None:
                iface.remove(it)

    # panels in first-use order
    wanted_panels = []
    for spec in specs:
        if spec.in_out == "INPUT" and spec.panel and spec.panel not in wanted_panels:
            wanted_panels.append(spec.panel)
    panels = {p.name: p for p in _panels(iface)}
    for name in wanted_panels:
        if name not in panels:
            panels[name] = iface.new_panel(name)

    created = set()
    for spec in specs:
        if spec.key not in matched:
            parent = panels.get(spec.panel) if spec.in_out == "INPUT" and spec.panel else None
            if parent is not None:
                it = iface.new_socket(spec.name, in_out=spec.in_out, socket_type=spec.socket_type, parent=parent)
            else:
                it = iface.new_socket(spec.name, in_out=spec.in_out, socket_type=spec.socket_type)
            matched[spec.key] = it.identifier
            created.add(spec.key)

    root = _root(iface)
    # top level: outputs, then inputs without a panel, then the panels
    top = [s for s in specs if s.in_out == "OUTPUT"] + [s for s in specs if s.in_out == "INPUT" and not s.panel]
    for pos, spec in enumerate(top):
        it = _by_identifier(iface, matched[spec.key])
        if it is None:
            continue
        if _panel_name(it) is not None and root is not None:
            iface.move_to_parent(it, root, pos)
        elif it.position != pos:
            iface.move(it, pos)
    for name in wanted_panels:
        members = [s for s in specs if s.in_out == "INPUT" and s.panel == name]
        for pos, spec in enumerate(members):
            it = _by_identifier(iface, matched[spec.key])
            if it is None:
                continue
            if _panel_name(it) != name:
                iface.move_to_parent(it, panels[name], pos)
            elif it.position != pos:
                iface.move(it, pos)
    for p in list(_panels(iface)):
        if p.name not in wanted_panels:
            iface.remove(p)
    if root is not None:
        base = len(top)
        for k, name in enumerate(wanted_panels):
            p = panels[name]
            if p.position != base + k:
                iface.move(p, base + k)

    for spec in specs:
        if spec.vtype == "GEOMETRY" or spec.in_out != "INPUT":
            continue
        it = _by_identifier(iface, matched[spec.key])
        if hasattr(it, "description"):
            it.description = spec.description or ""
        if spec.vtype in ("FLOAT", "INT"):
            lo = -1.0e9 if spec.min is None else spec.min
            hi = 1.0e9 if spec.max is None else spec.max
            if spec.vtype == "INT":
                lo, hi = int(max(lo, -2**31)), int(min(hi, 2**31 - 1))
            it.min_value, it.max_value = lo, hi
        if spec.default is not None and hasattr(it, "default_value"):
            try:
                it.default_value = spec.default
            except (TypeError, ValueError):
                pass
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
    "GeometryNodeSetPosition": 0, "GeometryNodeSetID": 0, "ShaderNodeFloatCurve": 9,
    "ShaderNodeValToRGB": 7, "ShaderNodeTexVoronoi": 3, "GeometryNodeDistributePointsOnFaces": 1,
    "GeometryNodeDeleteGeometry": 2, "GeometryNodeFieldToList": 1, "FunctionNodeFormatString": 1,
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
    "ROTATION": ("FunctionNodeInputRotation", "rotation_euler"),
    "RGBA": ("FunctionNodeInputColor", "value"),
    "STRING": ("FunctionNodeInputString", "string"),
}


def _adapt(sock, v):
    """Fit a compiler constant to a socket's default_value."""
    t = sock.type
    if t == "RGBA" and isinstance(v, (tuple, list)) and len(v) == 3:
        return tuple(v) + (1.0,)
    if t == "RGBA" and isinstance(v, (int, float)) and not isinstance(v, bool):
        return (float(v),) * 3 + (1.0,)
    if t == "ROTATION" and isinstance(v, (tuple, list)) and len(v) == 4:
        return quat_to_euler(*v)
    return v


def _const_node(tree, cache, sock_type, value):
    key = (sock_type, value if not isinstance(value, list) else tuple(value))
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


def _add_items(b, n):
    attr = n.items_attr or "capture_items"
    coll = getattr(b, attr)
    if attr == "index_switch_items":
        while len(coll) < len(n.items):
            coll.new()
        return
    items = n.items
    if attr in ("repeat_items", "state_items"):
        items = items[len(coll):]       # keep the default Geometry item (and its socket identifiers)
    for stype, name in items:
        coll.new(stype, name)


def materialize(result, tree, do_layout=True):
    graph = result.graph
    saved_ramps = ramps.save_all(tree)
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

    occurrences = {}
    for n in graph.nodes:
        b = made[n.uid]
        if n.pair is not None:
            b.pair_with_output(made[n.pair.uid])
    for n in graph.nodes:
        b = made[n.uid]
        if n.items:
            _add_items(b, n)
        if n.value is not None:
            b.outputs[0].default_value = n.value
        if n.ramp is not None:
            kind, name, preset = n.ramp
            occ = occurrences.get((kind, name), 0)
            occurrences[(kind, name)] = occ + 1
            ramps.apply(b, kind, name, preset, saved_ramps.get((kind, name, occ)))

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
            if isinstance(v, list):
                # multi-input socket: link in order (Join Geometry, Join Strings)
                for x in reversed(v) if sock.is_multi_input else v:
                    src = x if isinstance(x, Out) else None
                    if src is None:
                        raise BuildError(f"internal: {n.idname}.{key} expects links")
                    links.new(_socket(made[src.node.uid], src.sock, ids, outputs=True), sock)
                continue
            if isinstance(v, Out):
                src = _socket(made[v.node.uid], v.sock, ids, outputs=True)
                links.new(src, sock)
            elif sock.hide_value and sock.identifier not in _HONORS_DEFAULT and sock.type in _CONST_NODE:
                cn = _const_node(tree, const_cache, sock.type, _adapt(sock, v))
                if cn not in extra:
                    extra.append(cn)
                links.new(cn.outputs[0], sock)
            else:
                try:
                    sock.default_value = _adapt(sock, v)
                except (TypeError, ValueError, AttributeError) as e:
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


def build_group(result, source, mode, output="AUTO", name="Formula", target=None, version="", runover=None):
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
    tree[RUNOVER_KEY] = runover or getattr(result, "runover", "POINT")
    has_geo = any(s.key == "out:Geometry" for s in result.iface)
    if hasattr(tree, "is_modifier"):
        tree.is_modifier = has_geo
    if hasattr(tree, "description"):
        first = next((ln.strip() for ln in source.strip().splitlines()
                      if ln.strip() and not ln.strip().startswith(("//", "#"))), "")
        tree.description = f"Formula to Nodes: {first[:80]}"
    return tree
