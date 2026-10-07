# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared building blocks of the compiler: types, the node-graph IR, typed
values and the function registry. Pure Python (no bpy)."""

import math

from .lang import FormulaError, is_list, elem_type, list_type

__all__ = [
    "FLOAT", "INT", "BOOL", "VECTOR", "STRING", "ROTATION", "MATRIX", "COLOR", "FLOAT2",
    "OBJECT", "COLLECTION", "MATERIAL", "IMAGE", "SOUND", "GEOMETRY", "VOID",
    "TYPE_WORD", "type_word", "ATTR_DTYPE", "READ_DTYPE", "SOCKET_DTYPE", "ITEM_TYPE", "SOCKET_TYPE",
    "PREFIX_TYPE", "RESOURCE_TYPES", "is_list", "elem_type", "list_type",
    "Out", "Node", "Graph", "IfaceSocket", "Val", "FuncDef", "FUNCS", "reg",
    "CATEGORY_ORDER", "FormulaError", "quat_to_euler", "euler_to_quat", "zero_of",
]

FLOAT, INT, BOOL, VECTOR, STRING = "FLOAT", "INT", "BOOL", "VECTOR", "STRING"
ROTATION, MATRIX = "ROTATION", "MATRIX"
COLOR, FLOAT2 = "COLOR", "FLOAT2"          # storage flavours of VECTOR attributes
OBJECT, COLLECTION, MATERIAL, IMAGE, SOUND = "OBJECT", "COLLECTION", "MATERIAL", "IMAGE", "SOUND"
GEOMETRY, VOID = "GEOMETRY", "VOID"
RESOURCE_TYPES = (OBJECT, COLLECTION, MATERIAL, IMAGE, SOUND)

TYPE_WORD = {FLOAT: "float", INT: "int", BOOL: "bool", VECTOR: "vector", STRING: "string",
             ROTATION: "rotation (vector4)", MATRIX: "matrix", COLOR: "color", OBJECT: "object",
             COLLECTION: "collection", MATERIAL: "material", IMAGE: "image", SOUND: "sound",
             GEOMETRY: "geometry", VOID: "void"}


def type_word(t):
    if is_list(t):
        return type_word(elem_type(t)) + "[]"
    return TYPE_WORD.get(t, str(t).lower())


# Store Named Attribute / Named Attribute / Sample Index data types
ATTR_DTYPE = {FLOAT: "FLOAT", INT: "INT", BOOL: "BOOLEAN", VECTOR: "FLOAT_VECTOR",
              ROTATION: "QUATERNION", MATRIX: "FLOAT4X4", STRING: "STRING",
              COLOR: "FLOAT_COLOR", FLOAT2: "FLOAT2"}
# data types for reading (Named Attribute, Sample Index...): 2D vectors are read as 3D
READ_DTYPE = dict(ATTR_DTYPE, FLOAT2="FLOAT_VECTOR")
# socket_type / input_type enums (Switch, lists, bundles, capture items)
SOCKET_DTYPE = {FLOAT: "FLOAT", INT: "INT", BOOL: "BOOLEAN", VECTOR: "VECTOR", ROTATION: "ROTATION",
                MATRIX: "MATRIX", STRING: "STRING", COLOR: "RGBA", OBJECT: "OBJECT",
                COLLECTION: "COLLECTION", MATERIAL: "MATERIAL", IMAGE: "IMAGE", SOUND: "SOUND",
                GEOMETRY: "GEOMETRY"}
ITEM_TYPE = SOCKET_DTYPE
SOCKET_TYPE = {FLOAT: "NodeSocketFloat", INT: "NodeSocketInt", BOOL: "NodeSocketBool",
               VECTOR: "NodeSocketVector", ROTATION: "NodeSocketRotation", MATRIX: "NodeSocketMatrix",
               STRING: "NodeSocketString", COLOR: "NodeSocketColor", OBJECT: "NodeSocketObject",
               COLLECTION: "NodeSocketCollection", MATERIAL: "NodeSocketMaterial",
               IMAGE: "NodeSocketImage", SOUND: "NodeSocketSound", GEOMETRY: "NodeSocketGeometry"}
# attribute prefixes: f@ i@ b@ v@ s@ like VEX, p@ (vector4 → rotation), 3@/4@ (matrix),
# c@ (colour attribute), u@ (2D vector, e.g. UVs)
PREFIX_TYPE = {"f": FLOAT, "i": INT, "b": BOOL, "v": VECTOR, "s": STRING, "p": ROTATION,
               "3": MATRIX, "4": MATRIX, "c": VECTOR, "u": VECTOR}
PREFIX_STORE = {"c": COLOR, "u": FLOAT2}


def zero_of(t):
    return {FLOAT: 0.0, INT: 0, BOOL: False, VECTOR: (0.0, 0.0, 0.0), STRING: "",
            ROTATION: (0.0, 0.0, 0.0), MATRIX: ("ident",)}.get(t)


# ── rotation constants are stored as XYZ Euler angles (what Blender's
#    rotation sockets take); literals arrive as quaternions {x, y, z, w} ──

def quat_to_euler(x, y, z, w):
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if n == 0:
        return (0.0, 0.0, 0.0)
    x, y, z, w = x / n, y / n, z / n, w / n
    # XYZ Euler, matching Blender (mathutils Quaternion.to_euler('XYZ'))
    sinr = 2.0 * (w * x + y * z)
    cosr = 1.0 - 2.0 * (x * x + y * y)
    ex = math.atan2(sinr, cosr)
    sinp = 2.0 * (w * y - z * x)
    ey = math.copysign(math.pi / 2, sinp) if abs(sinp) >= 1 else math.asin(sinp)
    siny = 2.0 * (w * z + x * y)
    cosy = 1.0 - 2.0 * (y * y + z * z)
    ez = math.atan2(siny, cosy)
    return (ex, ey, ez)


def euler_to_quat(ex, ey, ez):
    cx, sx = math.cos(ex / 2), math.sin(ex / 2)
    cy, sy = math.cos(ey / 2), math.sin(ey / 2)
    cz, sz = math.cos(ez / 2), math.sin(ez / 2)
    w = cx * cy * cz + sx * sy * sz
    x = sx * cy * cz - cx * sy * sz
    y = cx * sy * cz + sx * cy * sz
    z = cx * cy * sz - sx * sy * cz
    return (x, y, z, w)


# ═════════════════════════════════════════════════════════════════════════════
#  IR
# ═════════════════════════════════════════════════════════════════════════════

class Out:
    __slots__ = ("node", "sock")

    def __init__(self, node, sock):
        self.node, self.sock = node, sock

    def key(self):
        return ("o", self.node.uid, self.sock)

    def __repr__(self):
        return f"<{self.node.idname}#{self.node.uid}.{self.sock}>"


class Node:
    __slots__ = ("uid", "idname", "props", "inputs", "pure", "label", "items", "items_attr",
                 "pair", "value", "visible", "ramp", "extra")

    def __init__(self, uid, idname, props, inputs, pure):
        self.uid, self.idname, self.props, self.inputs, self.pure = uid, idname, props, inputs, pure
        self.label = None
        self.items = None       # dynamic items: [(socket_type, name)] on the node's items collection
        self.items_attr = None  # name of that collection (capture_items, repeat_items, ...)
        self.pair = None        # zone input → its output node
        self.value = None       # ShaderNodeValue constant
        self.visible = None     # NodeGroupInput: the one interface key it shows
        self.ramp = None        # (kind, name, preset) for Float Curve / Color Ramp nodes
        self.extra = None       # free-form build hints

    def out(self, sock):
        return Out(self, sock)


def _key(v):
    if isinstance(v, Out):
        return v.key()
    if isinstance(v, tuple):
        return ("t",) + tuple(_key(x) if isinstance(x, (Out, tuple)) else x for x in v)
    if isinstance(v, list):
        return ("l",) + tuple(_key(x) for x in v)
    return (type(v).__name__, v)


class IfaceSocket:
    __slots__ = ("key", "in_out", "name", "vtype", "default", "min", "max", "explicit",
                 "panel", "description", "single")

    def __init__(self, key, in_out, name, vtype, default=None, vmin=None, vmax=None, explicit=False,
                 panel=None, description="", single=False):
        self.key, self.in_out, self.name, self.vtype = key, in_out, name, vtype
        self.default, self.min, self.max, self.explicit = default, vmin, vmax, explicit
        self.panel, self.description, self.single = panel, description, single

    @property
    def socket_type(self):
        return SOCKET_TYPE[self.vtype]


class Graph:
    def __init__(self):
        self.nodes = []
        self._cse = {}

    def add(self, idname, props=None, inputs=None, pure=True, value=None,
            visible=None, items=None, label=None, items_attr=None, ramp=None):
        props = dict(props or {})
        inputs = dict(inputs or {})
        key = None
        if pure and ramp is None:
            key = (idname,
                   tuple(sorted((k, _key(v)) for k, v in props.items())),
                   tuple(sorted((k, _key(v)) for k, v in inputs.items())),
                   None if value is None else _key(value), visible,
                   None if items is None else tuple(items))
            hit = self._cse.get(key)
            if hit is not None:
                return hit
        n = Node(len(self.nodes), idname, props, inputs, pure)
        n.value, n.visible, n.items, n.label = value, visible, items, label
        n.items_attr, n.ramp = items_attr, ramp
        self.nodes.append(n)
        if key is not None:
            self._cse[key] = n
        return n

    def prune(self, roots):
        """Drop every node that can't reach one of ``roots``."""
        live, stack = set(), list(roots)
        while stack:
            n = stack.pop()
            if n.uid in live:
                continue
            live.add(n.uid)
            for v in n.inputs.values():
                if isinstance(v, Out):
                    stack.append(v.node)
                elif isinstance(v, list):
                    stack.extend(x.node for x in v if isinstance(x, Out))
            if n.pair is not None:
                stack.append(n.pair)
        changed = True
        while changed:              # zone outputs pull in their inputs (and what feeds them)
            changed = False
            for n in self.nodes:
                if n.pair is not None and n.pair.uid in live and n.uid not in live:
                    live.add(n.uid)
                    stack = [n]
                    while stack:
                        m = stack.pop()
                        for v in m.inputs.values():
                            vals = v if isinstance(v, list) else [v]
                            for x in vals:
                                if isinstance(x, Out) and x.node.uid not in live:
                                    live.add(x.node.uid)
                                    stack.append(x.node)
                    changed = True
        self.nodes = [n for n in self.nodes if n.uid in live]
        return live


# ═════════════════════════════════════════════════════════════════════════════
#  Values
# ═════════════════════════════════════════════════════════════════════════════

class Val:
    """A typed value: constant (o is None, c holds it) or socket (o is an Out).
    ``field`` means it depends on per-element geometry context. For arrays
    ``c`` may hold the static length when it is known."""
    __slots__ = ("t", "c", "o", "field")

    def __init__(self, t, c=None, o=None, field=False):
        self.t, self.c, self.o, self.field = t, c, o, field

    @property
    def is_const(self):
        return self.o is None

    def __repr__(self):
        return f"Val({self.t}, {'c=' + repr(self.c) if self.o is None else self.o}{', field' if self.field else ''})"


# ═════════════════════════════════════════════════════════════════════════════
#  Function registry (drives the compiler, the Reference panel and the AI prompt)
# ═════════════════════════════════════════════════════════════════════════════

class FuncDef:
    __slots__ = ("name", "sig", "doc", "category", "handler", "data", "feature", "effect")

    def __init__(self, name, sig, doc, category, handler, data, feature=None, effect=False):
        self.name, self.sig, self.doc, self.category, self.handler, self.data = \
            name, sig, doc, category, handler, data
        self.feature, self.effect = feature, effect


FUNCS = {}
CATEGORY_ORDER = ["Math", "Vector", "Mapping & blending", "Noise & random", "Color",
                  "Rotation & matrix", "Geometry", "Sampling", "Topology", "Aggregates",
                  "Arrays", "Text & messages", "Parameters", "Effects", "Geometry operations",
                  "Conversion"]


def reg(names, sig, doc, category, handler, feature=None, effect=False, **data):
    """Register one or more names (space separated) for a built-in function.
    ``feature`` is a caps.FEATURES key the function needs; ``effect`` marks
    statement-only functions that change the geometry."""
    for n in names.split():
        FUNCS[n] = FuncDef(n, sig.replace("NAME", n), doc, category, handler, dict(data), feature, effect)
