# SPDX-License-Identifier: GPL-3.0-or-later
"""Compiler: parsed program → node-graph IR.

Pure Python (no bpy). ``build.py`` turns the IR into a real node tree.
Because validation *is* this compile step, a script that passes
``check()`` is guaranteed to have well-typed, zone-legal links.

Key ideas
  * Every expression evaluates to a typed ``Val``: either a Python constant
    (inlined into socket default values — no Value nodes) or an output
    socket reference in the IR.
  * Pure nodes are hash-consed, so identical sub-expressions and repeated
    attribute reads share one node.
  * Local variables have VEX value semantics: if a local reads geometry and
    is used after a later write changes that geometry, it is captured
    (Capture Attribute) right before the write.
  * Lexical scopes make zone boundaries safe: nothing declared inside a
    simulate/repeat/foreach block can be referenced after its '}'.
"""

import ast
import bisect
import difflib
import math

from .lang import (FormulaError, parse_program, walk, split_attr_placeholder,
                   SAssign, SDecl, SReturn, SExpr, SBlock)

FLOAT, INT, BOOL, VECTOR, STRING = "FLOAT", "INT", "BOOL", "VECTOR", "STRING"
TYPE_WORD = {FLOAT: "float", INT: "int", BOOL: "bool", VECTOR: "vector", STRING: "text"}
_RANK = {BOOL: 0, INT: 1, FLOAT: 2, VECTOR: 3}
ATTR_DTYPE = {FLOAT: "FLOAT", INT: "INT", BOOL: "BOOLEAN", VECTOR: "FLOAT_VECTOR"}
ITEM_TYPE = {FLOAT: "FLOAT", INT: "INT", BOOL: "BOOLEAN", VECTOR: "VECTOR"}
SOCKET_TYPE = {FLOAT: "NodeSocketFloat", INT: "NodeSocketInt",
               BOOL: "NodeSocketBool", VECTOR: "NodeSocketVector"}
PREFIX_TYPE = {"f": FLOAT, "i": INT, "b": BOOL, "v": VECTOR, "s": VECTOR}

MATH_IN = ("Value", "Value_001", "Value_002")
VM_IN = ("Vector", "Vector_001", "Vector_002")
VM_SCALAR_OUT = {"DOT_PRODUCT", "DISTANCE", "LENGTH"}


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
    __slots__ = ("uid", "idname", "props", "inputs", "pure", "label",
                 "items", "pair", "value", "visible")

    def __init__(self, uid, idname, props, inputs, pure):
        self.uid, self.idname, self.props, self.inputs, self.pure = uid, idname, props, inputs, pure
        self.label = None
        self.items = None      # capture items: [(socket_type, name)]
        self.pair = None       # zone input → its output node
        self.value = None      # ShaderNodeValue constant
        self.visible = None    # NodeGroupInput: the one interface key it shows

    def out(self, sock):
        return Out(self, sock)


def _key(v):
    if isinstance(v, Out):
        return v.key()
    if isinstance(v, tuple):
        return ("t",) + tuple(float(x) for x in v)
    return (type(v).__name__, v)


class IfaceSocket:
    __slots__ = ("key", "in_out", "name", "vtype", "default", "min", "max", "explicit")

    def __init__(self, key, in_out, name, vtype, default=None, vmin=None, vmax=None, explicit=False):
        self.key, self.in_out, self.name, self.vtype = key, in_out, name, vtype
        self.default, self.min, self.max, self.explicit = default, vmin, vmax, explicit

    @property
    def socket_type(self):
        return "NodeSocketGeometry" if self.vtype == "GEOMETRY" else SOCKET_TYPE[self.vtype]


class Graph:
    def __init__(self):
        self.nodes = []
        self._cse = {}

    def add(self, idname, props=None, inputs=None, pure=True, value=None,
            visible=None, items=None, label=None):
        props = dict(props or {})
        inputs = dict(inputs or {})
        key = None
        if pure:
            key = (idname,
                   tuple(sorted((k, _key(v)) for k, v in props.items())),
                   tuple(sorted((k, _key(v)) for k, v in inputs.items())),
                   None if value is None else _key(value), visible)
            hit = self._cse.get(key)
            if hit is not None:
                return hit
        n = Node(len(self.nodes), idname, props, inputs, pure)
        n.value, n.visible, n.items, n.label = value, visible, items, label
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
            if n.pair is not None:
                stack.append(n.pair)
        for n in self.nodes:            # zone outputs pull in their inputs
            if n.pair is not None and n.pair.uid in live and n.uid not in live:
                live.add(n.uid)
        self.nodes = [n for n in self.nodes if n.uid in live]
        return live


# ═════════════════════════════════════════════════════════════════════════════
#  Values, scopes
# ═════════════════════════════════════════════════════════════════════════════

class Val:
    """A typed value: constant (o is None, c holds it) or socket (o is an Out).
    ``field`` means it depends on per-element geometry context."""
    __slots__ = ("t", "c", "o", "field")

    def __init__(self, t, c=None, o=None, field=False):
        self.t, self.c, self.o, self.field = t, c, o, field

    @property
    def is_const(self):
        return self.o is None


class Ctx:
    """A geometry chain: the root, or the inside of one zone."""
    __slots__ = ("parent", "kind", "geo", "mark", "reserved")

    def __init__(self, parent, kind, geo, mark, reserved=None):
        self.parent, self.kind, self.geo, self.mark = parent, kind, geo, mark
        self.reserved = reserved or {}


class Scope:
    __slots__ = ("parent", "ctx", "kind", "bindings", "declared", "cond", "negate")

    def __init__(self, parent, ctx, kind, cond=None, negate=False):
        self.parent, self.ctx, self.kind = parent, ctx, kind
        self.bindings, self.declared = {}, set()
        self.cond, self.negate = cond, negate


class Binding:
    __slots__ = ("name", "t", "lazy", "mark", "ctx", "captures", "line")

    def __init__(self, name, t, lazy, ctx, line):
        self.name, self.t, self.lazy, self.ctx, self.line = name, t, lazy, ctx, line
        self.mark = ctx.mark
        self.captures = []     # [(ctx, mark, Val)]


class Attr:
    __slots__ = ("name", "t", "kind", "node", "sock", "field")

    def __init__(self, name, t, kind, node=None, sock=None, field=True):
        self.name, self.t, self.kind, self.node, self.sock, self.field = name, t, kind, node, sock, field


# ═════════════════════════════════════════════════════════════════════════════
#  Language tables
# ═════════════════════════════════════════════════════════════════════════════

VEX_ALIASES = {"P": "position", "N": "normal", "ptnum": "index", "pscale": "radius",
               "v": "velocity", "Frame": "frame", "Time": "time", "numpt": "numpt"}

# name → (node idname, output socket, type, writable-as)
BUILTIN_ATTRS = {
    "position":    ("GeometryNodeInputPosition", "Position", VECTOR, "position"),
    "normal":      ("GeometryNodeInputNormal", "Normal", VECTOR, "normal"),
    "index":       ("GeometryNodeInputIndex", "Index", INT, None),
    "id":          ("GeometryNodeInputID", "ID", INT, "id"),
    "radius":      ("GeometryNodeInputRadius", "Radius", FLOAT, "store"),
    "tilt":        ("GeometryNodeInputCurveTilt", "Tilt", FLOAT, "store"),
    "curveparam":  ("GeometryNodeSplineParameter", "Factor", FLOAT, None),
    "curvelength": ("GeometryNodeSplineParameter", "Length", FLOAT, None),
    "is_cyclic":   ("GeometryNodeInputSplineCyclic", "Cyclic", BOOL, None),
    "frame":       ("GeometryNodeInputSceneTime", "Frame", FLOAT, None),
    "time":        ("GeometryNodeInputSceneTime", "Seconds", FLOAT, None),
}
NON_FIELD_BUILTINS = {"frame", "time"}
NAMED_ATTR_TYPES = {"tangent": VECTOR, "rest_position": VECTOR, "velocity": VECTOR}
READONLY_HINTS = {
    "index": "the element index is implicit — store a copy instead, e.g. i@my_index = @ptnum",
    "curveparam": "it's computed from the curve — store your value under a new name",
    "curvelength": "it's computed from the curve — store your value under a new name",
    "is_cyclic": "use a Set Spline Cyclic node for that",
    "frame": "scene time is read-only",
    "time": "scene time is read-only",
    "up": "'up' is the constant {0, 0, 1}",
    "numpt": "the point count is read-only",
}

CONSTANTS = {"pi": (FLOAT, math.pi), "PI": (FLOAT, math.pi), "M_PI": (FLOAT, math.pi),
             "e": (FLOAT, math.e), "tau": (FLOAT, math.tau),
             "true": (BOOL, True), "false": (BOOL, False)}

RESERVED = {
    "deltatime": ("sim", "a simulate { } block"),
    "elemindex": ("foreach", "a foreach { } block"),
    "iteration": ("repeat", "a repeat(n) { } block"),
}

TYPE_NAMES = {"float", "int", "vector", "vector3", "bool"}


def _promote(*ts):
    return max(ts, key=lambda t: _RANK.get(t, 0))


# ── constant folding with Blender's exact "safe" math semantics ──────────────

def _pow(a, b):
    if a >= 0 or b == math.floor(b):
        return a ** b
    return 0.0


def _log(a, b):
    if a > 0 and b > 0:
        d = math.log(b)
        return math.log(a) / d if d != 0 else 0.0
    return 0.0


def _clamp1(a):
    return max(-1.0, min(1.0, a))


MATH_FOLD = {
    "ADD": lambda a, b: a + b, "SUBTRACT": lambda a, b: a - b,
    "MULTIPLY": lambda a, b: a * b, "DIVIDE": lambda a, b: a / b if b != 0 else 0.0,
    "POWER": _pow, "LOGARITHM": _log,
    "SQRT": lambda a: math.sqrt(a) if a > 0 else 0.0,
    "INVERSE_SQRT": lambda a: 1.0 / math.sqrt(a) if a > 0 else 0.0,
    "ABSOLUTE": abs, "EXPONENT": math.exp, "MINIMUM": min, "MAXIMUM": max,
    "SIGN": lambda a: float((a > 0) - (a < 0)),
    "ROUND": lambda a: math.floor(a + 0.5), "FLOOR": math.floor, "CEIL": math.ceil,
    "TRUNC": math.trunc, "FRACT": lambda a: a - math.floor(a),
    "MODULO": lambda a, b: math.fmod(a, b) if b != 0 else 0.0,
    "FLOORED_MODULO": lambda a, b: a - math.floor(a / b) * b if b != 0 else 0.0,
    "SINE": math.sin, "COSINE": math.cos, "TANGENT": math.tan,
    "ARCSINE": lambda a: math.asin(_clamp1(a)), "ARCCOSINE": lambda a: math.acos(_clamp1(a)),
    "ARCTANGENT": math.atan, "ARCTAN2": math.atan2,
    "SINH": math.sinh, "COSH": math.cosh, "TANH": math.tanh,
    "RADIANS": math.radians, "DEGREES": math.degrees,
}


def _fold(fn, *args):
    try:
        r = fn(*args)
    except (ValueError, OverflowError, ZeroDivisionError, TypeError):
        return None
    if isinstance(r, tuple):
        return None if any(math.isnan(x) or math.isinf(x) for x in r) else tuple(float(x) for x in r)
    r = float(r)
    return None if (math.isnan(r) or math.isinf(r)) else r


def _vfold(op, vs, s):
    def cw(fn, *vecs):
        return tuple(fn(*xs) for xs in zip(*vecs))
    a = vs[0]
    b = vs[1] if len(vs) > 1 else None
    if op == "ADD":
        return cw(lambda x, y: x + y, a, b)
    if op == "SUBTRACT":
        return cw(lambda x, y: x - y, a, b)
    if op == "MULTIPLY":
        return cw(lambda x, y: x * y, a, b)
    if op == "DIVIDE":
        return cw(lambda x, y: x / y if y != 0 else 0.0, a, b)
    if op == "SCALE":
        return tuple(x * s for x in a)
    if op == "LENGTH":
        return math.sqrt(sum(x * x for x in a))
    if op == "DOT_PRODUCT":
        return sum(x * y for x, y in zip(a, b))
    if op == "DISTANCE":
        return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))
    if op == "CROSS_PRODUCT":
        return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
    if op == "NORMALIZE":
        ln = math.sqrt(sum(x * x for x in a))
        return tuple(x / ln for x in a) if ln != 0 else (0.0, 0.0, 0.0)
    if op == "ABSOLUTE":
        return cw(abs, a)
    if op == "MINIMUM":
        return cw(min, a, b)
    if op == "MAXIMUM":
        return cw(max, a, b)
    if op == "FLOOR":
        return cw(math.floor, a)
    if op == "CEIL":
        return cw(math.ceil, a)
    if op == "FRACTION":
        return cw(lambda x: x - math.floor(x), a)
    if op == "SINE":
        return cw(math.sin, a)
    if op == "COSINE":
        return cw(math.cos, a)
    if op == "TANGENT":
        return cw(math.tan, a)
    if op == "SIGN":
        return cw(lambda x: float((x > 0) - (x < 0)), a)
    if op == "MODULO":
        return cw(lambda x, y: math.fmod(x, y) if y != 0 else 0.0, a, b)
    raise TypeError("no fold")


# ═════════════════════════════════════════════════════════════════════════════
#  Function registry (drives the compiler, the Reference panel and the AI prompt)
# ═════════════════════════════════════════════════════════════════════════════

class FuncDef:
    __slots__ = ("name", "sig", "doc", "category", "handler", "data")

    def __init__(self, name, sig, doc, category, handler, data):
        self.name, self.sig, self.doc, self.category, self.handler, self.data = \
            name, sig, doc, category, handler, data


FUNCS = {}
CATEGORY_ORDER = ["Math", "Vector", "Mapping & blending", "Noise & random",
                  "Geometry", "Parameters", "Conversion"]


def _reg(names, sig, doc, category, handler, **data):
    for n in names.split():
        FUNCS[n] = FuncDef(n, sig.replace("NAME", n), doc, category, handler, dict(data))


# Math — work on floats; the "poly" ones also work component-wise on vectors
for _n, _m, _v in [("sin", "SINE", "SINE"), ("cos", "COSINE", "COSINE"), ("tan", "TANGENT", "TANGENT"),
                   ("abs", "ABSOLUTE", "ABSOLUTE"), ("sign", "SIGN", "SIGN"),
                   ("floor", "FLOOR", "FLOOR"), ("ceil", "CEIL", "CEIL"),
                   ("round", "ROUND", "ROUND"), ("rint", "ROUND", "ROUND"),
                   ("fract", "FRACT", "FRACTION"), ("frac", "FRACT", "FRACTION")]:
    _reg(_n, "NAME(x)", "float or per-component on vectors", "Math", "f_poly", mop=_m, vop=_v, n=1)
for _n, _m in [("asin", "ARCSINE"), ("acos", "ARCCOSINE"), ("sinh", "SINH"), ("cosh", "COSH"),
               ("tanh", "TANH"), ("sqrt", "SQRT"), ("invsqrt", "INVERSE_SQRT"), ("exp", "EXPONENT"),
               ("trunc", "TRUNC"), ("radians", "RADIANS"), ("degrees", "DEGREES")]:
    _reg(_n, "NAME(x)", "float", "Math", "f_poly", mop=_m, vop=None, n=1)
for _n, _m, _v in [("pow", "POWER", "POWER"), ("mod", "MODULO", "MODULO"), ("fmod", "MODULO", "MODULO"),
                   ("snap", "SNAP", "SNAP")]:
    _reg(_n, "NAME(a, b)", "float or per-component on vectors", "Math", "f_poly", mop=_m, vop=_v, n=2)
for _n, _m in [("atan2", "ARCTAN2"), ("flooredmod", "FLOORED_MODULO"), ("pingpong", "PINGPONG"),
               ("less", "LESS_THAN"), ("greater", "GREATER_THAN")]:
    _reg(_n, "NAME(a, b)", "float", "Math", "f_poly", mop=_m, vop=None, n=2)
_reg("wrap", "wrap(x, max, min)", "wraps x into [min, max); floats or vectors", "Math", "f_poly",
     mop="WRAP", vop="WRAP", n=3)
_reg("multiplyadd", "multiplyadd(a, b, c)", "a * b + c", "Math", "f_poly", mop="MULTIPLY_ADD", vop="MULTIPLY_ADD", n=3)
_reg("smoothmin smoothmax", "NAME(a, b, distance)", "soft min / max", "Math", "f_poly",
     mop=None, vop=None, n=3)
FUNCS["smoothmin"].data["mop"] = "SMOOTH_MIN"
FUNCS["smoothmax"].data["mop"] = "SMOOTH_MAX"
_reg("min max", "NAME(a, b, ...)", "2 or more values; per-component on vectors", "Math", "f_minmax")
_reg("atan", "atan(x) or atan(y, x)", "arc tangent", "Math", "f_atan")
_reg("log", "log(x) or log(x, base)", "natural log by default", "Math", "f_log")

# Vector
_reg("length", "length(v)", "vector length", "Vector", "f_vec", op="LENGTH", n=1)
_reg("normalize", "normalize(v)", "unit vector (zero stays zero)", "Vector", "f_vec", op="NORMALIZE", n=1)
_reg("dot", "dot(a, b)", "dot product", "Vector", "f_vec", op="DOT_PRODUCT", n=2)
_reg("cross", "cross(a, b)", "cross product", "Vector", "f_vec", op="CROSS_PRODUCT", n=2)
_reg("distance", "distance(a, b)", "distance between points", "Vector", "f_vec", op="DISTANCE", n=2)
_reg("project", "project(a, b)", "projects a onto b", "Vector", "f_vec", op="PROJECT", n=2)
_reg("reflect", "reflect(v, normal)", "reflection", "Vector", "f_vec", op="REFLECT", n=2)
_reg("faceforward", "faceforward(v, incident, reference)", "orients v", "Vector", "f_vec", op="FACEFORWARD", n=3)
_reg("refract", "refract(v, normal, ior)", "refraction", "Vector", "f_refract")
_reg("scale", "scale(v, s)", "multiplies a vector by a float", "Vector", "f_scale")
_reg("length2", "length2(v)", "squared length", "Vector", "f_length2")
_reg("distance2", "distance2(a, b)", "squared distance", "Vector", "f_distance2")
for _n, _v in [("vabs", "ABSOLUTE"), ("vfloor", "FLOOR"), ("vceil", "CEIL"), ("vfract", "FRACTION"),
               ("vsin", "SINE"), ("vcos", "COSINE"), ("vtan", "TANGENT")]:
    _reg(_n, "NAME(v)", "per-component on a vector", "Vector", "f_vec", op=_v, n=1)
for _n, _v in [("vmin", "MINIMUM"), ("vmax", "MAXIMUM"), ("vmod", "MODULO"), ("vsnap", "SNAP")]:
    _reg(_n, "NAME(a, b)", "per-component on vectors", "Vector", "f_vec", op=_v, n=2)
_reg("vwrap", "vwrap(v, max, min)", "per-component wrap", "Vector", "f_vec", op="WRAP", n=3)
_reg("rotate", "rotate(v, axis, angle)", "rotates v around axis (radians)", "Vector", "f_rotate")

# Mapping & blending
_reg("fit", "fit(x, old_min, old_max, new_min, new_max)", "remap, clamped; floats or vectors",
     "Mapping & blending", "f_fit", clamp=True)
_reg("efit", "efit(x, old_min, old_max, new_min, new_max)", "remap without clamping",
     "Mapping & blending", "f_fit", clamp=False)
_reg("fit01", "fit01(x, new_min, new_max)", "remap from 0..1", "Mapping & blending", "f_fit", clamp=True, src=(0.0, 1.0))
_reg("fit10", "fit10(x, new_min, new_max)", "remap from 1..0", "Mapping & blending", "f_fit", clamp=True, src=(1.0, 0.0))
_reg("fit11", "fit11(x, new_min, new_max)", "remap from -1..1", "Mapping & blending", "f_fit", clamp=True, src=(-1.0, 1.0))
_reg("smoothstep smooth", "NAME(edge0, edge1, x)", "0..1 smooth ramp", "Mapping & blending", "f_smoothstep")
_reg("clamp", "clamp(x, min, max)", "floats or vectors", "Mapping & blending", "f_clamp")
_reg("lerp mix", "NAME(a, b, t)", "linear blend; floats or vectors", "Mapping & blending", "f_lerp")

# Noise & random
_reg("noise", "noise(pos) or noise(pos, scale)", "3D noise, 0..1", "Noise & random", "f_noise", mode="fac")
_reg("snoise", "snoise(pos) or snoise(pos, scale)", "3D noise, -1..1", "Noise & random", "f_noise", mode="signed")
_reg("vnoise", "vnoise(pos) or vnoise(pos, scale)", "3D noise as a vector, 0..1", "Noise & random", "f_noise", mode="color")
_reg("rand", "rand(seed)", "0..1 random from a seed, e.g. rand(@ptnum)", "Noise & random", "f_rand")
_reg("random", "random() / random(min, max) / random(min, max, seed)", "per-element random value",
     "Noise & random", "f_random")

# Geometry
_reg("nearpoint", "nearpoint() or nearpoint(pos)", "index of the nearest OTHER point", "Geometry", "f_nearpoint")
_reg("point", 'point(0, "attr", index)', 'reads an attribute at another point, e.g. point(0, "P", i)',
     "Geometry", "f_point")
_reg("npoints", "npoints()", "number of points", "Geometry", "f_npoints")
_reg("relbbox", "relbbox(pos)", "position inside the bounding box, 0..1 per axis", "Geometry", "f_bbox", which="rel")
_reg("getbbox_min", "getbbox_min()", "bounding box minimum", "Geometry", "f_bbox", which="min")
_reg("getbbox_max", "getbbox_max()", "bounding box maximum", "Geometry", "f_bbox", which="max")
_reg("getbbox_center", "getbbox_center()", "bounding box center", "Geometry", "f_bbox", which="center")
_reg("getbbox_size", "getbbox_size()", "bounding box size", "Geometry", "f_bbox", which="size")

# Parameters
_reg("chf ch", 'NAME("name", default, min=, max=)', "float slider on the node", "Parameters", "f_param", t=FLOAT)
_reg("chi", 'chi("name", default, min=, max=)', "integer field on the node", "Parameters", "f_param", t=INT)
_reg("chv", 'chv("name", {x, y, z})', "vector field on the node", "Parameters", "f_param", t=VECTOR)
_reg("chb", 'chb("name", default)', "checkbox on the node", "Parameters", "f_param", t=BOOL)

# Conversion
_reg("set vec vector", "NAME(x, y, z) or NAME(x)", "builds a vector", "Conversion", "f_vector")
_reg("float", "float(x)", "to float", "Conversion", "f_cast", t=FLOAT)
_reg("int", "int(x)", "to int (truncates)", "Conversion", "f_cast", t=INT)
_reg("bool", "bool(x)", "to bool (non-zero is true)", "Conversion", "f_cast", t=BOOL)
_reg("getcomp", "getcomp(v, i)", "component 0, 1 or 2 of a vector", "Conversion", "f_getcomp")

UNSUPPORTED = {
    "volumegradient": "no node computes a volume gradient on point fields — build that part by hand",
    "volumesample": "sample volumes with the Sample Grid node by hand",
    "xyzdist": "use a Geometry Proximity node by hand",
    "primuv": "use Sample Nearest Surface by hand",
    "intersect": "use a Raycast node by hand",
    "pcopen": "point clouds lookups aren't available — try nearpoint() or point()",
    "pcfind": "point clouds lookups aren't available — try nearpoint()",
    "nearpoints": "only nearpoint() (single nearest point) is available",
    "neighbours": "mesh topology queries aren't available",
    "neighbourcount": "mesh topology queries aren't available",
    "addpoint": "geometry can't be created from a script — use Points / Instance nodes",
    "addprim": "geometry can't be created from a script",
    "removepoint": "use a Delete Geometry node by hand",
    "removeprim": "use a Delete Geometry node by hand",
    "setpointattrib": "assign directly instead: f@name = value",
    "setattrib": "assign directly instead: f@name = value",
    "printf": "printing isn't available — return a value or store it in an attribute to inspect it",
    "sprintf": "text isn't supported",
    "chramp": "ramps aren't available — use fit(), smoothstep() or lerp()",
    "lookat": "matrices aren't supported",
    "maketransform": "matrices aren't supported",
    "quaternion": "rotations are only available via rotate(v, axis, angle)",
    "qrotate": "use rotate(v, axis, angle)",
    "chs": "text parameters aren't supported",
}


# ═════════════════════════════════════════════════════════════════════════════
#  Results
# ═════════════════════════════════════════════════════════════════════════════

class CompileResult:
    def __init__(self, graph, iface, notes, mode):
        self.graph, self.iface, self.notes, self.mode = graph, iface, notes, mode

    @property
    def node_count(self):
        return len(self.graph.nodes)


def compile_source(source, mode="SCRIPT", output="AUTO", strict=False):
    """Compile or raise FormulaError. mode: 'SCRIPT' | 'FORMULA'.
    output (formula mode): 'AUTO' | 'GEOMETRY' | 'FLOAT'."""
    return Compiler(strict=strict).run(source, mode, output)


def check(source, mode="SCRIPT", output="AUTO", strict=False):
    """(ok, error_or_None, notes). Never raises for user mistakes."""
    try:
        res = compile_source(source, mode, output, strict)
    except FormulaError as e:
        return False, e, []
    return True, None, res.notes


# ═════════════════════════════════════════════════════════════════════════════
#  Compiler
# ═════════════════════════════════════════════════════════════════════════════

class Compiler:
    def __init__(self, strict=False):
        self.strict = strict
        self.g = Graph()
        self.notes = []
        self._noted = set()
        self.params = {}          # name → IfaceSocket (first-use order)
        self.attr_types = {}      # custom attribute name → type seen in this script
        self.reads = {}           # name → sorted statement ids that read it
        self.zone_limit = float("inf")
        self._mark = 0
        self.cur_line = None
        self.cur_sid = -1
        self.cur_text = ""
        self.writes = 0
        self.return_val = None
        self.closed_names = {}    # local name → (block word, line) after its block closed

    # ── entry ──────────────────────────────────────────────────────────────
    def run(self, source, mode, output):
        if not source or not source.strip():
            raise FormulaError("the script is empty")
        stmts = parse_program(source)
        if not stmts:
            raise FormulaError("the script is empty — it only contains comments")

        self.geo_node = self.g.add("NodeGroupInput", visible="param:Geometry")
        self.geo_in = self.geo_node.out("param:Geometry")
        self.root_ctx = Ctx(None, "root", self.geo_in, self._new_mark())
        self.scope = self.root_scope = Scope(None, self.root_ctx, "root")
        self.ctx = self.root_ctx
        self._compute_reads(stmts)

        expression_mode = len(stmts) == 1 and isinstance(stmts[0], SExpr)
        if expression_mode:
            self._at(stmts[0])
            try:
                result = self.expr(stmts[0].value)
                if output == "GEOMETRY":
                    raise FormulaError("Geometry output needs an assignment, e.g. f@mask = " + stmts[0].text)
                if output == "FLOAT":
                    result = self.coerce(result, FLOAT, "the formula result")
            except FormulaError as e:
                raise self._locate(e, stmts[0]) from None
            self.return_val = result
        else:
            if mode == "FORMULA" and output == "FLOAT" and not any(isinstance(s, SReturn) for s in stmts):
                pass  # an assignment formula still outputs geometry, as before
            for i, s in enumerate(stmts):
                if isinstance(s, SReturn) and i != len(stmts) - 1:
                    raise FormulaError("return must be the last statement", s.line)
            self.stmts(stmts)

        return self._finish(mode)

    def _finish(self, mode):
        wrote = self.root_ctx.geo is not self.geo_in
        if not wrote and self.return_val is None:
            raise FormulaError("nothing to build — assign to an attribute (f@name = ...) or return a value")
        inputs = {}
        if wrote:
            inputs["out:Geometry"] = self.root_ctx.geo
        rtype = None
        if self.return_val is not None:
            rv = self.return_val
            if rv.t == STRING:
                raise FormulaError("the result can't be text")
            rtype = rv.t
            inputs["out:Result"] = rv.o if rv.o is not None else self.const_out(rv).o
        gout = self.g.add("NodeGroupOutput", inputs=inputs, pure=False)
        self.g.prune([gout])

        iface = []
        if wrote:
            iface.append(IfaceSocket("out:Geometry", "OUTPUT", "Geometry", "GEOMETRY"))
        if rtype:
            iface.append(IfaceSocket("out:Result", "OUTPUT", "Result", rtype))
        live_keys = {n.visible for n in self.g.nodes if n.idname == "NodeGroupInput"}
        if "param:Geometry" in live_keys:
            iface.append(IfaceSocket("param:Geometry", "INPUT", "Geometry", "GEOMETRY"))
        for name, p in self.params.items():
            if p.key in live_keys:
                iface.append(p)
            else:
                self.note(f"parameter '{name}' isn't used by anything, so it was left out")
        return CompileResult(self.g, iface, self.notes, mode)

    # ── bookkeeping ────────────────────────────────────────────────────────
    def _new_mark(self):
        self._mark += 1
        return self._mark

    def note(self, msg):
        if msg not in self._noted:
            self._noted.add(msg)
            self.notes.append(msg if self.cur_line is None else f"Line {self.cur_line}: {msg}")

    def _at(self, s):
        self.cur_line, self.cur_sid, self.cur_text = s.line, s.sid, getattr(s, "text", "")

    @staticmethod
    def _locate(e, s):
        if e.line is None:
            e.line = s.line
        return e

    def _compute_reads(self, stmts):
        reads = self.reads

        def add(name, sid):
            reads.setdefault(name, []).append(sid)

        for s in walk(stmts):
            exprs = []
            if isinstance(s, (SAssign, SReturn, SExpr)):
                exprs.append(s.value)
            if isinstance(s, SDecl) and s.value is not None:
                exprs.append(s.value)
            if isinstance(s, SBlock) and isinstance(s.arg, ast.AST):
                exprs.append(s.arg)
            if isinstance(s, SAssign):
                exprs.append(s.target)
            names = {n.id for e in exprs for n in ast.walk(e) if isinstance(n, ast.Name)}
            for nm in names:
                add(nm, s.sid)
            if isinstance(s, SBlock) and s.kind == "if":
                cname = f"if#{s.sid}"
                inner = list(walk(s.body)) + (list(walk(s.orelse)) if s.orelse else [])
                for st in inner:
                    if isinstance(st, SBlock) and st.kind != "if":
                        add(cname, st.sid)
                    elif isinstance(st, SAssign):
                        base = st.target if isinstance(st.target, ast.Name) else st.target.value
                        if split_attr_placeholder(base.id):
                            add(cname, st.sid)
                        else:
                            add(cname, s.end_sid + 0.5)
        for v in reads.values():
            v.sort()

    def _read_between(self, name, lo, hi):
        """Is ``name`` read by a statement with lo < sid <= hi?"""
        sids = self.reads.get(name)
        if not sids:
            return False
        i = bisect.bisect_right(sids, lo)
        return i < len(sids) and sids[i] <= hi

    # ── statements ─────────────────────────────────────────────────────────
    def stmts(self, body):
        for s in body:
            self._at(s)
            try:
                if isinstance(s, SDecl):
                    self.s_decl(s)
                elif isinstance(s, SAssign):
                    self.s_assign(s)
                elif isinstance(s, SReturn):
                    self.s_return(s)
                elif isinstance(s, SExpr):
                    raise FormulaError(
                        f"'{s.text}' doesn't do anything — assign it to an attribute "
                        f"(f@name = ...) or a variable, or use return")
                elif isinstance(s, SBlock):
                    if s.kind == "if":
                        self.s_if(s)
                    else:
                        self.s_zone(s)
            except FormulaError as e:
                raise self._locate(e, s) from None

    def s_return(self, s):
        if self.scope is not self.root_scope:
            raise FormulaError("return must be at the top level, not inside a block")
        v = self.expr(s.value)
        if v.t == STRING:
            raise FormulaError("return can't return text")
        self.return_val = v

    def s_decl(self, s):
        self._check_new_name(s.name)
        if s.name in self.scope.declared:
            raise FormulaError(f"'{s.name}' is already declared in this block — assign to it instead: {s.name} = ...")
        if s.value is None:
            zero = {FLOAT: 0.0, INT: 0, BOOL: False, VECTOR: (0.0, 0.0, 0.0)}[s.vtype]
            val = Val(s.vtype, c=zero)
        else:
            val = self.coerce(self.expr(s.value), s.vtype, f"'{s.name}' (declared {TYPE_WORD[s.vtype]})")
        self._declare(s.name, s.vtype, val)

    def _declare(self, name, t, val):
        if name in self.params:
            raise FormulaError(f"'{name}' was already used as a slider before this line — "
                               f"declare it before using it, or pick another name")
        self.scope.bindings[name] = Binding(name, t, val, self.ctx, self.cur_line)
        self.scope.declared.add(name)

    def _check_new_name(self, name):
        if name in CONSTANTS or name in RESERVED:
            raise FormulaError(f"'{name}' is a built-in name and can't be used as a variable")
        if name in FUNCS:
            raise FormulaError(f"'{name}' is a function name — pick another variable name")
        if name in TYPE_NAMES:
            raise FormulaError(f"'{name}' is a type name")
        if split_attr_placeholder(name):
            raise FormulaError("attribute names can't be declared — assign them directly: f@name = ...")

    def s_assign(self, s):
        base, comp = self._target_parts(s.target)
        info = split_attr_placeholder(base)
        if info:
            self._write_attribute(info, comp, s)
        else:
            self._assign_local(base, comp, s)

    @staticmethod
    def _target_parts(t):
        if isinstance(t, ast.Name):
            return t.id, None
        if isinstance(t, ast.Attribute):
            if t.attr not in ("x", "y", "z"):
                raise FormulaError(f"unknown component '.{t.attr}' — use .x, .y or .z")
            return t.value.id, "xyz".index(t.attr)
        idx = t.slice
        if not (isinstance(idx, ast.Constant) and isinstance(idx.value, int) and 0 <= idx.value <= 2):
            raise FormulaError("component index must be 0, 1 or 2")
        return t.value.id, idx.value

    def _rhs(self, s, current):
        rhs = self.expr(s.value)
        if s.op is None:
            return rhs
        return self.binop(s.op, current(), rhs)

    # attribute writes ───────────────────────────────────────────────────────
    def _write_attribute(self, info, comp, s):
        prefix, raw = info
        attr = self.resolve_attr(prefix, raw)
        label = raw if raw == attr.name else f"{raw} ({attr.name})"
        if attr.kind in ("readonly", "const", "numpt"):
            hint = READONLY_HINTS.get(attr.name, "it's read-only")
            raise FormulaError(f"can't write to '{label}' — {hint}")

        # @position += offset → Set Position's Offset input (no extra nodes)
        if attr.kind == "position" and s.op in (ast.Add, ast.Sub):
            if comp is None:
                off = self.coerce(self.expr(s.value), VECTOR, "the position offset")
            else:
                d = self.coerce(self.expr(s.value), FLOAT, f"'{raw}.{'xyz'[comp]}'")
                parts = [Val(FLOAT, c=0.0)] * 3
                parts = list(parts)
                parts[comp] = d
                off = self.combine(parts)
            if s.op is ast.Sub:
                off = self.neg(off)
            self._emit_write(attr, offset=off)
            return

        if comp is None:
            val = self._rhs(s, lambda: self.read_attr(attr))
            val = self.coerce(val, attr.t, f"'{raw}' ({TYPE_WORD[attr.t]} attribute)")
        else:
            if attr.t != VECTOR:
                raise FormulaError(f"'.{'xyz'[comp]}' needs a vector — '{raw}' is a {TYPE_WORD[attr.t]} attribute")
            full = self.read_attr(attr)
            parts = [self.sep(full, i) for i in range(3)]
            cv = self._rhs(s, lambda: parts[comp])
            parts[comp] = self.coerce(cv, FLOAT, f"'{raw}.{'xyz'[comp]}'")
            val = self.combine(parts)
        self._emit_write(attr, value=val)

    def _emit_write(self, attr, value=None, offset=None):
        self.capture_live(after=self.cur_sid)
        sel = self.selection()
        geo = self.ctx.geo
        label = self.cur_text[:60] or None
        if attr.kind == "position":
            inputs = {"Geometry": geo}
            if offset is not None:
                inputs["Offset"] = self.inp(offset, VECTOR)
            else:
                inputs["Position"] = self.inp(value, VECTOR)
            if sel is not None:
                inputs["Selection"] = self.inp(sel, BOOL)
            node = self.g.add("GeometryNodeSetPosition", {}, inputs, pure=False, label=label)
            self.ctx.geo = node.out("Geometry")
        elif attr.kind == "normal":
            v = value if sel is None else self.switch(sel, self.read_attr(attr), value, VECTOR)
            node = self.g.add("GeometryNodeSetMeshNormal", {"mode": "FREE", "domain": "POINT"},
                              {"Mesh": geo, "Custom Normal": self.inp(v, VECTOR)}, pure=False, label=label)
            self.ctx.geo = node.out("Mesh")
        elif attr.kind == "id":
            inputs = {"Geometry": geo, "ID": self.inp(value, INT)}
            if sel is not None:
                inputs["Selection"] = self.inp(sel, BOOL)
            node = self.g.add("GeometryNodeSetID", {}, inputs, pure=False, label=label)
            self.ctx.geo = node.out("Geometry")
        else:
            inputs = {"Geometry": geo, "Name": attr.name, "Value": self.inp(value, attr.t)}
            if sel is not None:
                inputs["Selection"] = self.inp(sel, BOOL)
            node = self.g.add("GeometryNodeStoreNamedAttribute",
                              {"data_type": ATTR_DTYPE[attr.t], "domain": "POINT"},
                              inputs, pure=False, label=label)
            self.ctx.geo = node.out("Geometry")
            self.attr_types[attr.name] = attr.t
        self.writes += 1

    # local assignment ──────────────────────────────────────────────────────
    def _assign_local(self, name, comp, s):
        if name in RESERVED or name in CONSTANTS:
            raise FormulaError(f"'{name}' is built in and can't be assigned")
        found_scope, b = self.lookup(name)
        if b is None:
            if comp is not None:
                raise FormulaError(f"'{name}' isn't declared yet — declare it first: vector {name} = ...")
            if s.op is not None:
                raise FormulaError(f"'{name}' is used before it has a value — declare it first: float {name} = 0")
            self._check_new_name(name)
            val = self.expr(s.value)
            if val.t == STRING:
                raise FormulaError("variables can't hold text")
            self._declare(name, val.t, val)
            return

        sc = self.scope
        while sc is not found_scope:
            if sc.kind == "zone":
                raise FormulaError(
                    f"can't change '{name}' inside this block because it was declared outside it "
                    f"(values can't flow back out of simulate/repeat/foreach) — declare a new variable "
                    f"or store the value in an attribute")
            sc = sc.parent

        cur = self.read_binding(b)
        what = f"'{name}' (a {TYPE_WORD[b.t]})"
        if comp is None:
            val = self.coerce(self._rhs(s, lambda: cur), b.t, what)
        else:
            if b.t != VECTOR:
                raise FormulaError(f"'.{'xyz'[comp]}' needs a vector — '{name}' is a {TYPE_WORD[b.t]}")
            parts = [self.sep(cur, i) for i in range(3)]
            parts[comp] = self.coerce(self._rhs(s, lambda: parts[comp]), FLOAT, what)
            val = self.combine(parts)
        self.scope.bindings[name] = Binding(name, b.t, val, self.ctx, s.line)

    # blocks ────────────────────────────────────────────────────────────────
    def s_if(self, s):
        cond = self.as_bool(self.expr(s.arg), "the if condition")
        cname = f"if#{s.sid}"
        cond_b = Binding(cname, BOOL, cond, self.ctx, s.line)
        self.scope.bindings[cname] = cond_b

        t_scope = self._push("if", cond=cond_b)
        self.stmts(s.body)
        self._pop()
        t_over = {n: b for n, b in t_scope.bindings.items()
                  if n not in t_scope.declared and not n.startswith("if#")}

        e_over = {}
        if s.orelse:
            e_scope = self._push("if", cond=cond_b, negate=True)
            self.stmts(s.orelse)
            self._pop()
            e_over = {n: b for n, b in e_scope.bindings.items()
                      if n not in e_scope.declared and not n.startswith("if#")}
        self._at(s)

        names = list(t_over) + [n for n in e_over if n not in t_over]
        for name in names:
            _, outer = self.lookup(name)
            cond_now = self.read_binding(cond_b, warn=False)
            tv = self.read_binding(t_over[name]) if name in t_over else self.read_binding(outer)
            fv = self.read_binding(e_over[name]) if name in e_over else self.read_binding(outer)
            merged = self.switch(cond_now, fv, tv, outer.t)
            self.scope.bindings[name] = Binding(name, outer.t, merged, self.ctx, s.line)

    def s_zone(self, s):
        parent = self.ctx
        if s.kind == "repeat":
            count = self.coerce(self.expr(s.arg), INT, "the repeat count")
            if count.field:
                raise FormulaError("the repeat count must be a single number (e.g. 5 or chi(\"steps\", 5)), "
                                   "not a per-point value")
        if s.kind in ("repeat", "foreach") and self._has_effects(s.body):
            self.capture_live(after=s.sid - 0.5)

        label = s.text[:60] or None
        if s.kind == "sim":
            zi = self.g.add("GeometryNodeSimulationInput", {}, {"Item_0": parent.geo}, pure=False, label=label)
            zo = self.g.add("GeometryNodeSimulationOutput", {}, {}, pure=False)
            inner, reserved, mark = zi.out("Item_0"), {"deltatime": Val(FLOAT, o=zi.out("Delta Time"))}, self._new_mark()
            in_key = out_key = "Item_0"
        elif s.kind == "repeat":
            zi = self.g.add("GeometryNodeRepeatInput", {},
                            {"Iterations": self.inp(count, INT), "Item_0": parent.geo}, pure=False, label=label)
            zo = self.g.add("GeometryNodeRepeatOutput", {}, {}, pure=False)
            inner, reserved, mark = zi.out("Item_0"), {"iteration": Val(INT, o=zi.out("Iteration"))}, parent.mark
            in_key = out_key = "Item_0"
        else:
            zi = self.g.add("GeometryNodeForeachGeometryElementInput", {}, {"Geometry": parent.geo},
                            pure=False, label=label)
            zo = self.g.add("GeometryNodeForeachGeometryElementOutput", {"domain": s.arg}, {}, pure=False)
            inner, reserved, mark = zi.out("Element"), {"elemindex": Val(INT, o=zi.out("Index"))}, parent.mark
            in_key = out_key = "Generation_0"
        zi.pair = zo

        ctx = Ctx(parent, s.kind, inner, mark, reserved)
        saved_ctx, saved_limit = self.ctx, self.zone_limit
        self.ctx, self.zone_limit = ctx, s.end_sid
        self._push("zone")
        self.stmts(s.body)
        self._pop()
        self.ctx, self.zone_limit = saved_ctx, saved_limit
        self._at(s)

        zo.inputs[in_key] = ctx.geo
        parent.geo = zo.out(out_key)
        parent.mark = self._new_mark() if s.kind == "sim" else ctx.mark

    @staticmethod
    def _has_effects(body):
        for st in walk(body):
            if isinstance(st, SBlock) and st.kind != "if":
                return True
            if isinstance(st, SAssign) and isinstance(st.target, (ast.Name, ast.Attribute, ast.Subscript)):
                base = st.target if isinstance(st.target, ast.Name) else st.target.value
                if isinstance(base, ast.Name) and split_attr_placeholder(base.id):
                    return True
        return False

    def _push(self, kind, cond=None, negate=False):
        self.scope = Scope(self.scope, self.ctx, kind, cond, negate)
        return self.scope

    _BLOCK_WORD = {"zone": "a simulate/repeat/foreach block", "if": "an if/else block"}

    def _pop(self):
        closing = self.scope
        for name in closing.declared:
            self.closed_names[name] = (self._BLOCK_WORD.get(closing.kind, "a block"),
                                       closing.bindings[name].line)
        self.scope = closing.parent

    # ── bindings, captures, selection ──────────────────────────────────────
    def lookup(self, name):
        sc = self.scope
        while sc is not None:
            b = sc.bindings.get(name)
            if b is not None:
                return sc, b
            sc = sc.parent
        return None, None

    def _ctx_within(self, inner, outer):
        c = inner
        while c is not None:
            if c is outer:
                return True
            c = c.parent
        return False

    def valid_capture(self, b):
        for cctx, mark, v in reversed(b.captures):
            if mark == self.ctx.mark and self._ctx_within(self.ctx, cctx):
                return v
        return None

    def read_binding(self, b, warn=True):
        v = b.lazy
        if not v.field:
            return v
        cap = self.valid_capture(b)
        if cap is not None:
            return cap
        if warn and b.mark != self.ctx.mark and not b.name.startswith("if#"):
            self.note(f"'{b.name}' reads geometry and is re-evaluated after/inside a simulate block "
                      f"(simulations can't carry captured values). If you need its original value, "
                      f"store it first: {'v' if b.t == VECTOR else TYPE_WORD[b.t][0]}@{b.name} = ...")
        return v

    def capture_live(self, after):
        """Capture every geometry-dependent local that is read again after
        statement ``after`` but before the current zone closes (a capture made
        here can't be used outside this zone anyway)."""
        batch = []
        sc = self.scope
        while sc is not None:
            for b in sc.bindings.values():
                if (b.lazy.field and b not in batch
                        and self._read_between(b.name, after, self.zone_limit)
                        and self.valid_capture(b) is None):
                    batch.append(b)
            sc = sc.parent
        if not batch:
            return
        items, inputs = [], {"Geometry": self.ctx.geo}
        for k, b in enumerate(batch):
            nm = "condition" if b.name.startswith("if#") else b.name
            items.append((ITEM_TYPE[b.t], nm))
            inputs[f"item:{k}"] = b.lazy.o
        node = self.g.add("GeometryNodeCaptureAttribute", {"domain": "POINT"}, inputs,
                          pure=False, items=items, label="Capture " + ", ".join(i[1] for i in items)[:50])
        self.ctx.geo = node.out("Geometry")
        for k, b in enumerate(batch):
            b.captures.append((self.ctx, self.ctx.mark, Val(b.t, o=node.out(f"item:{k}"), field=True)))

    def selection(self):
        sel = None
        sc = self.scope
        while sc is not None:
            if sc.cond is not None:
                c = self.read_binding(sc.cond, warn=False)
                if sc.negate:
                    c = self.boolean("NOT", c)
                sel = c if sel is None else self.boolean("AND", c, sel)
            sc = sc.parent
        return sel

    # ── attributes ─────────────────────────────────────────────────────────
    def resolve_attr(self, prefix, raw):
        name = VEX_ALIASES.get(raw, raw)
        if name in BUILTIN_ATTRS:
            node, sock, t, write = BUILTIN_ATTRS[name]
            return Attr(name, t, write or "readonly", node, sock, field=name not in NON_FIELD_BUILTINS)
        if name == "up":
            return Attr("up", VECTOR, "const", field=False)
        if name == "numpt":
            return Attr("numpt", INT, "numpt", field=False)
        if prefix:
            t = PREFIX_TYPE[prefix]
        elif name in NAMED_ATTR_TYPES:
            t = NAMED_ATTR_TYPES[name]
        elif name in self.attr_types:
            t = self.attr_types[name]
        else:
            if self.strict:
                raise FormulaError(f"give '@{raw}' a type prefix: f@{raw} (float), v@{raw} (vector), "
                                   f"i@{raw} (int) or b@{raw} (bool)")
            self.note(f"'@{raw}' has no type prefix, so it's a vector attribute — "
                      f"write f@{raw} for a float")
            t = VECTOR
        return Attr(name, t, "store")

    def read_attr(self, a):
        if a.kind == "const":
            return Val(VECTOR, c=(0.0, 0.0, 1.0))
        if a.kind == "numpt":
            return self.f_npoints(None, "npoints")
        if a.node:
            n = self.g.add(a.node)
            return Val(a.t, o=n.out(a.sock), field=a.field)
        n = self.g.add("GeometryNodeInputNamedAttribute", {"data_type": ATTR_DTYPE[a.t]}, {"Name": a.name})
        return Val(a.t, o=n.out("Attribute"), field=True)

    # ── parameters ─────────────────────────────────────────────────────────
    def param(self, name, t, default=None, vmin=None, vmax=None, explicit=True):
        if not name or name in ("Geometry", "Result"):
            raise FormulaError(f"'{name}' can't be used as a parameter name")
        p = self.params.get(name)
        if p is None:
            p = IfaceSocket(f"param:{name}", "INPUT", name, t, default, vmin, vmax, explicit)
            self.params[name] = p
        else:
            if p.vtype != t:
                raise FormulaError(f"parameter '{name}' is used as both {TYPE_WORD[p.vtype]} and {TYPE_WORD[t]}")
            if explicit and not p.explicit:
                p.default, p.min, p.max, p.explicit = default, vmin, vmax, True
            elif explicit and default is not None and p.default != default:
                self.note(f"parameter '{name}' has two different defaults; using the first one")
        node = self.g.add("NodeGroupInput", visible=p.key)
        return Val(t, o=node.out(p.key), field=False)

    # ═══════════════════════════════════════════════════════════════════════
    #  Expressions
    # ═══════════════════════════════════════════════════════════════════════
    def expr(self, node):
        fn = getattr(self, "e_" + type(node).__name__, None)
        if fn is None:
            raise FormulaError(f"unsupported syntax: {ast.unparse(node)}")
        return fn(node)

    def e_Constant(self, node):
        v = node.value
        if isinstance(v, bool):
            return Val(BOOL, c=v)
        if isinstance(v, (int, float)):
            return Val(FLOAT, c=float(v))
        if isinstance(v, str):
            return Val(STRING, c=v)
        raise FormulaError(f"unsupported value {v!r}")

    def e_Name(self, node):
        name = node.id
        info = split_attr_placeholder(name)
        if info:
            return self.read_attr(self.resolve_attr(*info))
        _, b = self.lookup(name)
        if b is not None:
            return self.read_binding(b)
        c = self.ctx
        while c is not None:
            if name in c.reserved:
                return c.reserved[name]
            c = c.parent
        if name in RESERVED:
            raise FormulaError(f"'{name}' only exists inside {RESERVED[name][1]}")
        if name in CONSTANTS:
            t, v = CONSTANTS[name]
            return Val(t, c=v)
        if name in FUNCS or name in UNSUPPORTED:
            raise FormulaError(f"'{name}' is a function — call it with parentheses: {name}(...)")
        if name in TYPE_NAMES:
            raise FormulaError(f"'{name}' is a type — declare variables like: {name} my_var = ...")
        if name in self.closed_names:
            where, line = self.closed_names[name]
            raise FormulaError(f"'{name}' was declared inside {where} (line {line}) and doesn't exist "
                               f"after its closing '}}' — declare it before the block instead")
        return self._slider(name)

    def _slider(self, name):
        candidates = set(CONSTANTS) | set(RESERVED)
        sc = self.scope
        while sc is not None:
            candidates |= {n for n in sc.bindings if not n.startswith("if#")}
            sc = sc.parent
        close = difflib.get_close_matches(name, sorted(candidates), n=1, cutoff=0.75)
        if close and self.strict:
            raise FormulaError(f"unknown name '{name}' — did you mean '{close[0]}'?")
        if close:
            self.note(f"'{name}' became a slider — did you mean '{close[0]}'?")
        return self.param(name, FLOAT, 1.0, explicit=False)

    def e_BinOp(self, node):
        return self.binop(type(node.op), self.expr(node.left), self.expr(node.right))

    _OPNAME = {ast.Add: "ADD", ast.Sub: "SUBTRACT", ast.Mult: "MULTIPLY",
               ast.Div: "DIVIDE", ast.Mod: "MODULO", ast.Pow: "POWER"}
    _OPWORD = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.Mod: "%", ast.Pow: "**"}

    def binop(self, op, l, r):
        if op not in self._OPNAME:
            hint = {ast.BitAnd: " — use && for 'and'", ast.BitOr: " — use || for 'or'",
                    ast.FloorDiv: " — use floor(a / b)", ast.MatMult: ""}.get(op, "")
            raise FormulaError(f"unsupported operator{hint}")
        word = f"'{self._OPWORD[op]}'"
        for v in (l, r):
            if v.t == STRING:
                raise FormulaError(f"text can't be used with {word}")
        if l.t == VECTOR or r.t == VECTOR:
            if op is ast.Mult and (l.t != VECTOR or r.t != VECTOR):
                vec, s = (l, r) if l.t == VECTOR else (r, l)
                return self.vmath("SCALE", vec, scale=s)
            if op is ast.Div and r.t != VECTOR:
                rf = self.coerce(r, FLOAT)
                if rf.is_const:
                    return self.vmath("SCALE", l, scale=Val(FLOAT, c=(1.0 / rf.c) if rf.c != 0 else 0.0))
            return self.vmath(self._OPNAME[op], l, r)
        return self.math(self._OPNAME[op], l, r)

    def e_UnaryOp(self, node):
        v = self.expr(node.operand)
        if isinstance(node.op, ast.USub):
            if v.t == STRING:
                raise FormulaError("can't negate text")
            return self.neg(v)
        if isinstance(node.op, ast.UAdd):
            return v
        if isinstance(node.op, ast.Not):
            return self.boolean("NOT", v)
        raise FormulaError("'~' isn't supported — use ! for 'not'")

    def e_BoolOp(self, node):
        op = "AND" if isinstance(node.op, ast.And) else "OR"
        vals = [self.expr(v) for v in node.values]
        acc = vals[0]
        for v in vals[1:]:
            acc = self.boolean(op, acc, v)
        return self.as_bool(acc)

    _CMP = {ast.Lt: "LESS_THAN", ast.LtE: "LESS_EQUAL", ast.Gt: "GREATER_THAN",
            ast.GtE: "GREATER_EQUAL", ast.Eq: "EQUAL", ast.NotEq: "NOT_EQUAL"}

    def e_Compare(self, node):
        left = self.expr(node.left)
        acc = None
        for op, comp in zip(node.ops, node.comparators):
            right = self.expr(comp)
            if type(op) not in self._CMP:
                raise FormulaError("'in' / 'is' aren't supported — use == or !=")
            c = self.compare(self._CMP[type(op)], left, right)
            acc = c if acc is None else self.boolean("AND", acc, c)
            left = right
        return acc

    def e_IfExp(self, node):
        cond = self.as_bool(self.expr(node.test), "the ternary condition")
        a, b = self.expr(node.body), self.expr(node.orelse)
        if STRING in (a.t, b.t):
            raise FormulaError("a ternary can't produce text")
        return self.switch(cond, b, a)

    def e_Attribute(self, node):
        if node.attr not in ("x", "y", "z"):
            raise FormulaError(f"unknown component '.{node.attr}' — use .x, .y or .z")
        base = self.expr(node.value)
        if base.t != VECTOR:
            raise FormulaError(f"'.{node.attr}' needs a vector, but '{ast.unparse(node.value)}' "
                               f"is a {TYPE_WORD.get(base.t, base.t)}")
        return self.sep(base, "xyz".index(node.attr))

    def e_Subscript(self, node):
        idx = node.slice
        if not (isinstance(idx, ast.Constant) and isinstance(idx.value, int) and 0 <= idx.value <= 2):
            raise FormulaError("vector index must be 0, 1 or 2")
        base = self.expr(node.value)
        if base.t != VECTOR:
            raise FormulaError("[index] needs a vector")
        return self.sep(base, idx.value)

    def e_Set(self, node):
        if len(node.elts) != 3:
            raise FormulaError(f"vector literals need exactly 3 values, got {len(node.elts)}: {{x, y, z}}")
        return self.combine([self.expr(e) for e in node.elts])

    def e_Tuple(self, node):
        raise FormulaError("write vectors as {x, y, z} or set(x, y, z)")

    e_List = e_Tuple

    def e_Dict(self, node):
        raise FormulaError("empty or malformed braces — vectors look like {0, 0, 1}")

    def e_Call(self, node):
        if not isinstance(node.func, ast.Name):
            raise FormulaError(f"can't call '{ast.unparse(node.func)}'")
        name = node.func.id
        fd = FUNCS.get(name)
        if fd is None:
            if name in UNSUPPORTED:
                raise FormulaError(f"{name}() isn't supported — {UNSUPPORTED[name]}")
            if split_attr_placeholder(name):
                raise FormulaError("attributes aren't functions — remove the parentheses")
            close = difflib.get_close_matches(name, sorted(FUNCS), n=3, cutoff=0.6)
            hint = f" — did you mean {', '.join(c + '()' for c in close)}?" if close else ""
            raise FormulaError(f"unknown function '{name}()'{hint}")
        if node.keywords and fd.handler != "f_param":
            raise FormulaError(f"{name}() doesn't take named arguments")
        return getattr(self, fd.handler)(node, name)

    # ── argument helpers ───────────────────────────────────────────────────
    def args(self, node, name, counts):
        n = len(node.args)
        if n not in counts:
            want = " or ".join(str(c) for c in sorted(counts))
            raise FormulaError(f"{name}() takes {want} argument{'s' if max(counts) != 1 else ''}: "
                               f"{FUNCS[name].sig}")
        return [self.expr(a) for a in node.args]

    def _require_geo(self, fname):
        return self.ctx.geo

    # ═══════════════════════════════════════════════════════════════════════
    #  Node helpers (folding + CSE)
    # ═══════════════════════════════════════════════════════════════════════
    def coerce(self, v, t, what="this value"):
        if v.t == t:
            return v
        if v.t == STRING:
            raise FormulaError(f"{what} can't be text")
        if t == STRING:
            raise FormulaError(f"{what} must be text in quotes")
        if v.t == VECTOR:
            raise FormulaError(f"{what} must be a {TYPE_WORD[t]}, but got a vector — "
                               f"use a component (.x .y .z), length() or dot()")
        if v.is_const:
            c = v.c
            if t == VECTOR:
                return Val(VECTOR, c=(float(c),) * 3)
            if t == FLOAT:
                return Val(FLOAT, c=float(c))
            if t == INT:
                return Val(INT, c=int(float(c)))
            return Val(BOOL, c=float(c) > 0.0)
        return Val(t, o=v.o, field=v.field)

    def inp(self, v, t, what="this value"):
        v = self.coerce(v, t, what)
        if v.is_const:
            if t == VECTOR:
                return tuple(float(x) for x in v.c)
            return v.c
        return v.o

    def const_out(self, v):
        if v.t == FLOAT:
            n = self.g.add("ShaderNodeValue", value=float(v.c))
            return Val(FLOAT, o=n.out("Value"))
        if v.t == INT:
            return Val(INT, o=self.g.add("FunctionNodeInputInt", {"integer": int(v.c)}).out("Integer"))
        if v.t == BOOL:
            return Val(BOOL, o=self.g.add("FunctionNodeInputBool", {"boolean": bool(v.c)}).out("Boolean"))
        return Val(VECTOR, o=self.g.add("FunctionNodeInputVector",
                                         {"vector": tuple(float(x) for x in v.c)}).out("Vector"))

    def math(self, op, *args, what=None):
        word = what or f"the {op.lower().replace('_', ' ')} input"
        vals = [self.coerce(a, FLOAT, word) for a in args]
        if all(v.is_const for v in vals) and op in MATH_FOLD:
            r = _fold(MATH_FOLD[op], *[v.c for v in vals])
            if r is not None:
                return Val(FLOAT, c=r)
        if len(vals) == 2:
            a, b = vals
            if op in ("ADD", "SUBTRACT") and b.is_const and b.c == 0.0:
                return a
            if op == "ADD" and a.is_const and a.c == 0.0:
                return b
            if op in ("MULTIPLY", "DIVIDE", "POWER") and b.is_const and b.c == 1.0:
                return a
            if op == "MULTIPLY" and a.is_const and a.c == 1.0:
                return b
        n = self.g.add("ShaderNodeMath", {"operation": op},
                       {MATH_IN[i]: self.inp(v, FLOAT) for i, v in enumerate(vals)})
        return Val(FLOAT, o=n.out("Value"), field=any(v.field for v in vals))

    def vmath(self, op, *vecs, scale=None):
        vv = [self.coerce(v, VECTOR) for v in vecs]
        s = self.coerce(scale, FLOAT, "the scale") if scale is not None else None
        if all(v.is_const for v in vv) and (s is None or s.is_const):
            r = _fold(_vfold, op, [v.c for v in vv], s.c if s else None)
            if r is not None:
                return Val(FLOAT if op in VM_SCALAR_OUT else VECTOR, c=r)
        zero, one = (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)
        if op == "SCALE" and s.is_const and s.c == 1.0:
            return vv[0]
        if len(vv) == 2:
            a, b = vv
            if op in ("ADD", "SUBTRACT") and b.is_const and b.c == zero:
                return a
            if op == "ADD" and a.is_const and a.c == zero:
                return b
            if op in ("MULTIPLY", "DIVIDE") and b.is_const and b.c == one:
                return a
            if op == "MULTIPLY" and a.is_const and a.c == one:
                return b
        inputs = {VM_IN[i]: self.inp(v, VECTOR) for i, v in enumerate(vv)}
        if s is not None:
            inputs["Scale"] = self.inp(s, FLOAT)
        n = self.g.add("ShaderNodeVectorMath", {"operation": op}, inputs)
        field = any(v.field for v in vv) or bool(s is not None and s.field)
        if op in VM_SCALAR_OUT:
            return Val(FLOAT, o=n.out("Value"), field=field)
        return Val(VECTOR, o=n.out("Vector"), field=field)

    def neg(self, v):
        if v.t == VECTOR:
            return self.vmath("SCALE", v, scale=Val(FLOAT, c=-1.0))
        return self.math("MULTIPLY", v, Val(FLOAT, c=-1.0))

    def combine(self, parts):
        ps = [self.coerce(p, FLOAT, "a vector component") for p in parts]
        if all(p.is_const for p in ps):
            return Val(VECTOR, c=tuple(float(p.c) for p in ps))
        if all(not p.is_const for p in ps):
            n0 = ps[0].o.node
            if (n0.idname == "ShaderNodeSeparateXYZ" and all(p.o.node is n0 for p in ps)
                    and [p.o.sock for p in ps] == ["X", "Y", "Z"] and isinstance(n0.inputs.get("Vector"), Out)):
                return Val(VECTOR, o=n0.inputs["Vector"], field=ps[0].field)
        n = self.g.add("ShaderNodeCombineXYZ", {}, {k: self.inp(p, FLOAT) for k, p in zip("XYZ", ps)})
        return Val(VECTOR, o=n.out("Vector"), field=any(p.field for p in ps))

    def sep(self, v, i):
        v = self.coerce(v, VECTOR)
        if v.is_const:
            return Val(FLOAT, c=float(v.c[i]))
        src_node = v.o.node
        if src_node.idname == "ShaderNodeCombineXYZ":
            src = src_node.inputs.get("XYZ"[i], 0.0)
            if isinstance(src, Out):
                return Val(FLOAT, o=src, field=v.field)
            return Val(FLOAT, c=float(src))
        n = self.g.add("ShaderNodeSeparateXYZ", {}, {"Vector": v.o})
        return Val(FLOAT, o=n.out("XYZ"[i]), field=v.field)

    def as_bool(self, v, what="the condition"):
        if v.t == BOOL:
            return v
        if v.t == VECTOR:
            raise FormulaError(f"{what} is a vector — compare it instead, e.g. length(v) > 0")
        if v.t == STRING:
            raise FormulaError(f"{what} can't be text")
        if v.is_const:
            return Val(BOOL, c=float(v.c) != 0.0)
        if v.t == INT:
            n = self.g.add("FunctionNodeCompare", {"data_type": "INT", "operation": "NOT_EQUAL"},
                           {"A": v.o, "B": 0})
        else:
            n = self.g.add("FunctionNodeCompare", {"data_type": "FLOAT", "operation": "NOT_EQUAL"},
                           {"A": v.o, "B": 0.0, "Epsilon": 0.0})
        return Val(BOOL, o=n.out("Result"), field=v.field)

    def boolean(self, op, a, b=None):
        a = self.as_bool(a)
        if op == "NOT":
            if a.is_const:
                return Val(BOOL, c=not a.c)
            if a.o.node.idname == "FunctionNodeBooleanMath" and a.o.node.props.get("operation") == "NOT":
                src = a.o.node.inputs.get("Boolean")
                if isinstance(src, Out):
                    return Val(BOOL, o=src, field=a.field)
            n = self.g.add("FunctionNodeBooleanMath", {"operation": "NOT"}, {"Boolean": a.o})
            return Val(BOOL, o=n.out("Boolean"), field=a.field)
        b = self.as_bool(b)
        for x, y in ((a, b), (b, a)):
            if x.is_const:
                if op == "AND":
                    return y if x.c else Val(BOOL, c=False)
                return Val(BOOL, c=True) if x.c else y
        n = self.g.add("FunctionNodeBooleanMath", {"operation": op}, {"Boolean": a.o, "Boolean_001": b.o})
        return Val(BOOL, o=n.out("Boolean"), field=a.field or b.field)

    def compare(self, op, a, b):
        if STRING in (a.t, b.t):
            raise FormulaError("text can't be compared")
        if a.t == VECTOR or b.t == VECTOR:
            av, bv = self.coerce(a, VECTOR), self.coerce(b, VECTOR)
            if av.is_const and bv.is_const:
                pairs = list(zip(av.c, bv.c))
                fns = {"LESS_THAN": lambda x, y: x < y, "LESS_EQUAL": lambda x, y: x <= y,
                       "GREATER_THAN": lambda x, y: x > y, "GREATER_EQUAL": lambda x, y: x >= y,
                       "EQUAL": lambda x, y: abs(x - y) <= 0.001, "NOT_EQUAL": lambda x, y: abs(x - y) > 0.001}
                if op == "NOT_EQUAL":
                    return Val(BOOL, c=any(fns[op](x, y) for x, y in pairs))
                return Val(BOOL, c=all(fns[op](x, y) for x, y in pairs))
            n = self.g.add("FunctionNodeCompare", {"data_type": "VECTOR", "operation": op, "mode": "ELEMENT"},
                           {"A": self.inp(av, VECTOR), "B": self.inp(bv, VECTOR)})
            return Val(BOOL, o=n.out("Result"), field=av.field or bv.field)
        if a.t in (INT, BOOL) and b.t in (INT, BOOL):
            dt, t = "INT", INT
        else:
            dt, t = "FLOAT", FLOAT
        av, bv = self.coerce(a, t), self.coerce(b, t)
        if av.is_const and bv.is_const:
            x, y = float(av.c), float(bv.c)
            eps = 0.001 if dt == "FLOAT" else 0.0
            res = {"LESS_THAN": x < y, "LESS_EQUAL": x <= y, "GREATER_THAN": x > y,
                   "GREATER_EQUAL": x >= y, "EQUAL": abs(x - y) <= eps, "NOT_EQUAL": abs(x - y) > eps}[op]
            return Val(BOOL, c=res)
        n = self.g.add("FunctionNodeCompare", {"data_type": dt, "operation": op},
                       {"A": self.inp(av, t), "B": self.inp(bv, t)})
        return Val(BOOL, o=n.out("Result"), field=av.field or bv.field)

    def switch(self, cond, false_v, true_v, t=None):
        cond = self.as_bool(cond)
        t = t or _promote(false_v.t, true_v.t)
        fv, tv = self.coerce(false_v, t, "the 'false' value"), self.coerce(true_v, t, "the 'true' value")
        if cond.is_const:
            return tv if cond.c else fv
        if fv.is_const and tv.is_const and fv.c == tv.c:
            return tv
        if not fv.is_const and not tv.is_const and fv.o.key() == tv.o.key():
            return tv
        n = self.g.add("GeometryNodeSwitch", {"input_type": ITEM_TYPE[t]},
                       {"Switch": cond.o, "False": self.inp(fv, t), "True": self.inp(tv, t)})
        return Val(t, o=n.out("Output"), field=cond.field or fv.field or tv.field)

    # ═══════════════════════════════════════════════════════════════════════
    #  Built-in functions
    # ═══════════════════════════════════════════════════════════════════════
    def f_poly(self, node, name):
        d = FUNCS[name].data
        vals = self.args(node, name, {d["n"]})
        if any(v.t == VECTOR for v in vals):
            if d["vop"] is None:
                raise FormulaError(f"{name}() works on floats only — apply it to a component (.x .y .z)")
            return self.vmath(d["vop"], *vals)
        return self.math(d["mop"], *vals, what=f"{name}()'s argument")

    def f_minmax(self, node, name):
        if len(node.args) < 2:
            raise FormulaError(f"{name}() needs at least 2 arguments: {name}(a, b)")
        vals = [self.expr(a) for a in node.args]
        acc = vals[0]
        for v in vals[1:]:
            if acc.t == VECTOR or v.t == VECTOR:
                acc = self.vmath("MINIMUM" if name == "min" else "MAXIMUM", acc, v)
            else:
                acc = self.math("MINIMUM" if name == "min" else "MAXIMUM", acc, v, what=f"{name}()'s argument")
        return acc

    def f_atan(self, node, name):
        vals = self.args(node, name, {1, 2})
        if len(vals) == 1:
            return self.math("ARCTANGENT", vals[0], what="atan()'s argument")
        return self.math("ARCTAN2", vals[0], vals[1], what="atan()'s argument")

    def f_log(self, node, name):
        vals = self.args(node, name, {1, 2})
        base = vals[1] if len(vals) == 2 else Val(FLOAT, c=math.e)
        return self.math("LOGARITHM", vals[0], base, what="log()'s argument")

    def f_vec(self, node, name):
        d = FUNCS[name].data
        vals = self.args(node, name, {d["n"]})
        if d["op"] == "LENGTH" and vals[0].t != VECTOR:
            return self.math("ABSOLUTE", vals[0])
        return self.vmath(d["op"], *vals)

    def f_refract(self, node, name):
        v, n, ior = self.args(node, name, {3})
        return self.vmath("REFRACT", v, n, scale=ior)

    def f_scale(self, node, name):
        v, s = self.args(node, name, {2})
        return self.vmath("SCALE", v, scale=s)

    def f_length2(self, node, name):
        (v,) = self.args(node, name, {1})
        return self.vmath("DOT_PRODUCT", v, v)

    def f_distance2(self, node, name):
        a, b = self.args(node, name, {2})
        d = self.vmath("SUBTRACT", a, b)
        return self.vmath("DOT_PRODUCT", d, d)

    def f_rotate(self, node, name):
        v, axis, ang = self.args(node, name, {3})
        v, axis = self.coerce(v, VECTOR), self.coerce(axis, VECTOR)
        ang = self.coerce(ang, FLOAT, "rotate()'s angle")
        n = self.g.add("ShaderNodeVectorRotate", {"rotation_type": "AXIS_ANGLE"},
                       {"Vector": self.inp(v, VECTOR), "Axis": self.inp(axis, VECTOR),
                        "Angle": self.inp(ang, FLOAT)})
        return Val(VECTOR, o=n.out("Vector"), field=v.field or axis.field or ang.field)

    def f_fit(self, node, name):
        d = FUNCS[name].data
        if "src" in d:
            x, nmin, nmax = self.args(node, name, {3})
            omin, omax = Val(FLOAT, c=d["src"][0]), Val(FLOAT, c=d["src"][1])
        else:
            x, omin, omax, nmin, nmax = self.args(node, name, {5})
        vals = [x, omin, omax, nmin, nmax]
        if any(v.t == VECTOR for v in vals):
            keys = ("Vector", "From_Min_FLOAT3", "From_Max_FLOAT3", "To_Min_FLOAT3", "To_Max_FLOAT3")
            n = self.g.add("ShaderNodeMapRange",
                           {"data_type": "FLOAT_VECTOR", "interpolation_type": "LINEAR", "clamp": d["clamp"]},
                           {k: self.inp(v, VECTOR) for k, v in zip(keys, vals)})
            return Val(VECTOR, o=n.out("Vector"), field=any(v.field for v in vals))
        vals = [self.coerce(v, FLOAT, f"{name}()'s argument") for v in vals]
        if all(v.is_const for v in vals):
            xv, a, b, c, e = (v.c for v in vals)
            if a != b:
                t = (xv - a) / (b - a)
                if d["clamp"]:
                    t = max(0.0, min(1.0, t))
                return Val(FLOAT, c=c + t * (e - c))
        keys = ("Value", "From Min", "From Max", "To Min", "To Max")
        n = self.g.add("ShaderNodeMapRange",
                       {"data_type": "FLOAT", "interpolation_type": "LINEAR", "clamp": d["clamp"]},
                       {k: self.inp(v, FLOAT) for k, v in zip(keys, vals)})
        return Val(FLOAT, o=n.out("Result"), field=any(v.field for v in vals))

    def f_smoothstep(self, node, name):
        e0, e1, x = [self.coerce(v, FLOAT, f"{name}()'s argument") for v in self.args(node, name, {3})]
        n = self.g.add("ShaderNodeMapRange",
                       {"data_type": "FLOAT", "interpolation_type": "SMOOTHSTEP", "clamp": True},
                       {"Value": self.inp(x, FLOAT), "From Min": self.inp(e0, FLOAT),
                        "From Max": self.inp(e1, FLOAT), "To Min": 0.0, "To Max": 1.0})
        return Val(FLOAT, o=n.out("Result"), field=e0.field or e1.field or x.field)

    def f_clamp(self, node, name):
        x, lo, hi = self.args(node, name, {3})
        if VECTOR in (x.t, lo.t, hi.t):
            return self.vmath("MINIMUM", self.vmath("MAXIMUM", x, lo), hi)
        x, lo, hi = [self.coerce(v, FLOAT, "clamp()'s argument") for v in (x, lo, hi)]
        if x.is_const and lo.is_const and hi.is_const:
            return Val(FLOAT, c=min(max(x.c, lo.c), hi.c))
        n = self.g.add("ShaderNodeClamp", {"clamp_type": "MINMAX"},
                       {"Value": self.inp(x, FLOAT), "Min": self.inp(lo, FLOAT), "Max": self.inp(hi, FLOAT)})
        return Val(FLOAT, o=n.out("Result"), field=x.field or lo.field or hi.field)

    def f_lerp(self, node, name):
        a, b, t = self.args(node, name, {3})
        t = self.coerce(t, FLOAT, f"{name}()'s blend factor")
        if VECTOR in (a.t, b.t):
            a, b = self.coerce(a, VECTOR), self.coerce(b, VECTOR)
            if a.is_const and b.is_const and t.is_const:
                return Val(VECTOR, c=tuple(x + (y - x) * t.c for x, y in zip(a.c, b.c)))
            n = self.g.add("ShaderNodeMix", {"data_type": "VECTOR", "clamp_factor": False},
                           {"Factor_Float": self.inp(t, FLOAT), "A_Vector": self.inp(a, VECTOR),
                            "B_Vector": self.inp(b, VECTOR)})
            return Val(VECTOR, o=n.out("Result_Vector"), field=a.field or b.field or t.field)
        a, b = self.coerce(a, FLOAT, f"{name}()'s argument"), self.coerce(b, FLOAT, f"{name}()'s argument")
        if a.is_const and b.is_const and t.is_const:
            return Val(FLOAT, c=a.c + (b.c - a.c) * t.c)
        n = self.g.add("ShaderNodeMix", {"data_type": "FLOAT", "clamp_factor": False},
                       {"Factor_Float": self.inp(t, FLOAT), "A_Float": self.inp(a, FLOAT),
                        "B_Float": self.inp(b, FLOAT)})
        return Val(FLOAT, o=n.out("Result_Float"), field=a.field or b.field or t.field)

    def f_noise(self, node, name):
        vals = self.args(node, name, {1, 2})
        pos = self.coerce(vals[0], VECTOR)
        inputs = {"Vector": self.inp(pos, VECTOR)}
        field = pos.field
        if len(vals) == 2:
            sc = self.coerce(vals[1], FLOAT, f"{name}()'s scale")
            inputs["Scale"] = self.inp(sc, FLOAT)
            field = field or sc.field
        n = self.g.add("ShaderNodeTexNoise", {"noise_dimensions": "3D"}, inputs)
        mode = FUNCS[name].data["mode"]
        if mode == "color":
            return Val(VECTOR, o=n.out("Color"), field=field)
        fac = Val(FLOAT, o=n.out("Fac"), field=field)
        if mode == "signed":
            return self.math("SUBTRACT", self.math("MULTIPLY", fac, Val(FLOAT, c=2.0)), Val(FLOAT, c=1.0))
        return fac

    def f_rand(self, node, name):
        (seed,) = self.args(node, name, {1})
        seed = self.coerce(seed, INT, "rand()'s seed")
        n = self.g.add("FunctionNodeRandomValue", {"data_type": "FLOAT"},
                       {"Min": 0.0, "Max": 1.0, "ID": self.inp(seed, INT), "Seed": 0})
        return Val(FLOAT, o=n.out("Value"), field=seed.field)

    def f_random(self, node, name):
        vals = self.args(node, name, {0, 2, 3})
        inputs = {}
        if len(vals) >= 2:
            inputs["Min"] = self.inp(vals[0], FLOAT, "random()'s min")
            inputs["Max"] = self.inp(vals[1], FLOAT, "random()'s max")
        if len(vals) == 3:
            inputs["Seed"] = self.inp(vals[2], INT, "random()'s seed")
        n = self.g.add("FunctionNodeRandomValue", {"data_type": "FLOAT"}, inputs)
        return Val(FLOAT, o=n.out("Value"), field=True)

    def f_nearpoint(self, node, name):
        args = list(node.args)
        if len(args) == 2 and isinstance(args[0], ast.Constant) and args[0].value == 0:
            args = args[1:]
        if len(args) > 1:
            raise FormulaError("nearpoint() takes 0 or 1 arguments: nearpoint() or nearpoint(pos)")
        inputs = {}
        if args:
            inputs["Position"] = self.inp(self.expr(args[0]), VECTOR, "nearpoint()'s position")
        n = self.g.add("GeometryNodeIndexOfNearest", {}, inputs)
        return Val(INT, o=n.out("Index"), field=True)

    def f_point(self, node, name):
        if len(node.args) != 3:
            raise FormulaError('point() takes 3 arguments: point(0, "attr", index)')
        g, a, idx = node.args
        if not (isinstance(g, ast.Constant) and g.value == 0 and not isinstance(g.value, bool)):
            raise FormulaError("point()'s first argument must be 0 (the current geometry)")
        if not (isinstance(a, ast.Constant) and isinstance(a.value, str)):
            raise FormulaError('point()\'s second argument must be an attribute name in quotes, e.g. "P"')
        text = a.value.strip()
        prefix, raw = "", text
        if "@" in text:
            prefix, _, raw = text.partition("@")
            if prefix not in ("", "f", "i", "b", "v", "s"):
                raise FormulaError(f"point(): unknown prefix in '{text}'")
        if not raw.isidentifier():
            raise FormulaError(f"point(): '{text}' isn't a valid attribute name")
        attr_name = VEX_ALIASES.get(raw, raw)
        if not prefix and attr_name not in BUILTIN_ATTRS and attr_name not in NAMED_ATTR_TYPES \
                and attr_name not in self.attr_types:
            prefix = "f"
        attr = self.resolve_attr(prefix, raw)
        value = self.read_attr(attr)
        if value.is_const:
            return value
        index = self.coerce(self.expr(idx), INT, "point()'s index")
        n = self.g.add("GeometryNodeSampleIndex", {"data_type": ATTR_DTYPE[attr.t], "domain": "POINT", "clamp": False},
                       {"Geometry": self.ctx.geo, "Value": value.o, "Index": self.inp(index, INT)})
        return Val(attr.t, o=n.out("Value"), field=True)

    def f_npoints(self, node, name):
        if node is not None:
            if len(node.args) > 1 or (node.args and not (isinstance(node.args[0], ast.Constant)
                                                          and node.args[0].value == 0)):
                raise FormulaError("npoints() takes no arguments (or just 0)")
        n = self.g.add("GeometryNodeAttributeStatistic", {"data_type": "FLOAT", "domain": "POINT"},
                       {"Geometry": self.ctx.geo, "Attribute": 1.0})
        return Val(INT, o=n.out("Sum"), field=False)

    def f_bbox(self, node, name):
        which = FUNCS[name].data["which"]
        args = list(node.args)
        if args and isinstance(args[0], ast.Constant) and args[0].value == 0 \
                and (which != "rel" or len(args) == 2):
            args = args[1:]
        if which == "rel":
            if len(args) != 1:
                raise FormulaError("relbbox() takes 1 argument: relbbox(v@position)")
        elif args:
            raise FormulaError(f"{name}() takes no arguments")
        bb = self.g.add("GeometryNodeBoundBox", {}, {"Geometry": self.ctx.geo})
        mn, mx = Val(VECTOR, o=bb.out("Min")), Val(VECTOR, o=bb.out("Max"))
        if which == "min":
            return mn
        if which == "max":
            return mx
        if which == "center":
            return self.vmath("SCALE", self.vmath("ADD", mn, mx), scale=Val(FLOAT, c=0.5))
        size = self.vmath("SUBTRACT", mx, mn)
        if which == "size":
            return size
        pos = self.coerce(self.expr(args[0]), VECTOR, "relbbox()'s position")
        return self.vmath("DIVIDE", self.vmath("SUBTRACT", pos, mn), size)

    def f_param(self, node, name):
        t = FUNCS[name].data["t"]
        if not node.args or not (isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            raise FormulaError(f'{name}() needs a name in quotes, e.g. {name}("strength", 0.5)')
        pname = node.args[0].value.strip()
        rest = node.args[1:]
        kw = {k.arg: k.value for k in node.keywords}
        for k in kw:
            if k not in ("default", "min", "max"):
                raise FormulaError(f"{name}() doesn't know '{k}=' — use default=, min= or max=")

        def const_of(expr_node, want, what):
            v = self.coerce(self.expr(expr_node), want, what)
            if not v.is_const:
                raise FormulaError(f"{name}(\"{pname}\"): {what} must be a plain number")
            return v.c

        default = None
        if t == VECTOR and len(rest) == 3:
            default = tuple(const_of(e, FLOAT, "the default") for e in rest)
        elif len(rest) == 1:
            default = const_of(rest[0], t, "the default")
        elif len(rest) > 1:
            raise FormulaError(f'{name}() takes a name and one default: {FUNCS[name].sig}')
        if "default" in kw:
            default = const_of(kw["default"], t, "the default")
        if default is None:
            default = {FLOAT: 0.0, INT: 0, BOOL: False, VECTOR: (0.0, 0.0, 0.0)}[t]
        vmin = const_of(kw["min"], FLOAT, "min") if "min" in kw else None
        vmax = const_of(kw["max"], FLOAT, "max") if "max" in kw else None
        if t in (VECTOR, BOOL) and (vmin is not None or vmax is not None):
            raise FormulaError(f"{name}() doesn't support min/max")
        return self.param(pname, t, default, vmin, vmax, explicit=True)

    def f_vector(self, node, name):
        vals = self.args(node, name, {1, 3})
        if len(vals) == 1:
            return self.coerce(vals[0], VECTOR, f"{name}()'s argument")
        return self.combine(vals)

    def f_cast(self, node, name):
        (v,) = self.args(node, name, {1})
        t = FUNCS[name].data["t"]
        if t == BOOL:
            return self.as_bool(v, "bool()'s argument")
        if t == INT and v.t == FLOAT:
            return self.coerce(self.math("TRUNC", v), INT)
        return self.coerce(v, t, f"{name}()'s argument")

    def f_getcomp(self, node, name):
        if len(node.args) != 2 or not (isinstance(node.args[1], ast.Constant)
                                       and node.args[1].value in (0, 1, 2)):
            raise FormulaError("getcomp() takes a vector and 0, 1 or 2: getcomp(v, 2)")
        v = self.expr(node.args[0])
        if v.t != VECTOR:
            raise FormulaError("getcomp() needs a vector")
        return self.sep(v, int(node.args[1].value))


def reference_by_category():
    """{category: [FuncDef, ...]} with aliases collapsed, for docs and prompts."""
    seen, out = set(), {c: [] for c in CATEGORY_ORDER}
    for fd in FUNCS.values():
        key = (fd.sig, fd.doc, fd.category, fd.handler)
        if fd.handler in ("f_poly", "f_vec") and fd.sig.startswith(fd.name + "("):
            key = (fd.name,)
        if key in seen:
            continue
        seen.add(key)
        out.setdefault(fd.category, []).append(fd)
    return out
