# SPDX-License-Identifier: GPL-3.0-or-later
"""Nodes → script: turns a Geometry Nodes tree back into a Formula to Nodes
script (like going from a VOP network to a wrangle in Houdini).

Two halves:
  extract(tree)    bpy side: reads any node tree into plain data
  decompile(data)  pure Python: rebuilds the geometry chain (one statement
                   per Set Position / Store Named Attribute / ...), the field
                   expressions feeding it, captures (as locals), zones and
                   run-over blocks.

Nodes without an equivalent become notes, never silent changes.
"""

import math

# ═════════════════════════════════════════════════════════════════════════════
#  bpy side
# ═════════════════════════════════════════════════════════════════════════════

_SKIP_PROPS = {"rna_type", "name", "label", "location", "location_absolute", "width", "height", "dimensions",
               "parent", "select", "show_options", "show_preview", "hide", "mute", "show_texture",
               "use_custom_color", "color", "color_tag", "bl_idname", "bl_label", "bl_description", "bl_icon",
               "bl_static_type", "bl_width_default", "bl_width_min", "bl_width_max", "bl_height_default",
               "bl_height_min", "bl_height_max", "type", "inputs", "outputs", "internal_links", "is_active_output",
               "warning_propagation", "active_index", "active_item", "node_tree", "texture_mapping", "color_mapping",
               "mapping", "color_ramp", "paired_output", "image_user", "capture_items", "repeat_items",
               "state_items", "bake_items", "format_items", "index_switch_items", "list_items", "bundle_items",
               "enum_items", "enum_definition", "generation_items", "main_items", "input_items", "output_items",
               "textbox_state", "inspection_index", "is_active_output_input", "viewer_items", "debug_zone_body_lazy_function_graph",
               "debug_zone_lazy_function_graph"}


def _pyval(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if hasattr(v, "name") and hasattr(v, "rna_type") and hasattr(v, "users"):
        return {"id": type(v).__name__, "name": v.name}
    try:
        return [_pyval(x) for x in v]
    except TypeError:
        return str(v)


def extract(tree):
    """Plain-data snapshot of a node tree (for decompile())."""
    out = {"name": tree.name, "interface": [], "nodes": {}}
    for it in tree.interface.items_tree:
        if it.item_type != "SOCKET":
            continue
        parent = it.parent
        out["interface"].append({
            "identifier": it.identifier, "name": it.name, "in_out": it.in_out, "socket_type": it.socket_type,
            "default": _pyval(getattr(it, "default_value", None)),
            "min": getattr(it, "min_value", None), "max": getattr(it, "max_value", None),
            "panel": parent.name if parent is not None and parent.name else None,
            "description": getattr(it, "description", ""),
        })
    for n in tree.nodes:
        props = {}
        for p in n.bl_rna.properties:
            if p.identifier in _SKIP_PROPS or p.type in ("POINTER", "COLLECTION"):
                continue
            try:
                props[p.identifier] = _pyval(getattr(n, p.identifier))
            except Exception:
                pass
        rec = {"idname": n.bl_idname, "label": n.label, "props": props, "inputs": [], "outputs": [],
               "pair": None, "items": [], "extra": {}}
        for s in n.inputs:
            rec["inputs"].append({
                "id": s.identifier, "name": s.name, "type": s.type, "enabled": s.enabled and not s.is_unavailable,
                "hide_value": getattr(s, "hide_value", False),
                "links": [(l.from_node.name, l.from_socket.identifier) for l in s.links
                          if not l.is_muted and l.is_valid],
                "value": _pyval(getattr(s, "default_value", None)),
            })
        for s in n.outputs:
            rec["outputs"].append({"id": s.identifier, "name": s.name, "type": s.type,
                                   "enabled": s.enabled and not s.is_unavailable,
                                   "value": _pyval(getattr(s, "default_value", None))})
        paired = getattr(n, "paired_output", None)
        if paired is not None:
            rec["pair"] = paired.name
        for attr in ("capture_items", "repeat_items", "state_items", "list_items", "format_items", "bundle_items"):
            coll = getattr(n, attr, None)
            if coll is not None:
                rec["items"] = [(it.name, getattr(it, "socket_type", getattr(it, "data_type", ""))) for it in coll]
        if n.bl_idname in ("ShaderNodeFloatCurve", "ShaderNodeValToRGB"):
            rec["extra"]["ramp"] = n.get("ftn_ramp")
            rec["extra"]["preset"] = n.get("ftn_ramp_preset") or ""
        out["nodes"][n.name] = rec
    return out


# ═════════════════════════════════════════════════════════════════════════════
#  pure Python side
# ═════════════════════════════════════════════════════════════════════════════

ATOM, UNARY, POW, MUL, ADD, CMP, AND, OR, TERN = 100, 90, 80, 70, 60, 50, 40, 30, 20

_MATH_BIN = {"ADD": ("+", ADD), "SUBTRACT": ("-", ADD), "MULTIPLY": ("*", MUL), "DIVIDE": ("/", MUL)}
_MATH_FN = {"POWER": "pow", "LOGARITHM": "log", "SQRT": "sqrt", "INVERSE_SQRT": "invsqrt", "ABSOLUTE": "abs",
            "EXPONENT": "exp", "MINIMUM": "min", "MAXIMUM": "max", "LESS_THAN": "less", "GREATER_THAN": "greater",
            "SIGN": "sign", "SMOOTH_MIN": "smoothmin", "SMOOTH_MAX": "smoothmax", "ROUND": "round",
            "FLOOR": "floor", "CEIL": "ceil", "TRUNC": "trunc", "FRACT": "frac", "MODULO": "fmod",
            "FLOORED_MODULO": "flooredmod", "WRAP": "wrap", "SNAP": "snap", "PINGPONG": "pingpong",
            "SINE": "sin", "COSINE": "cos", "TANGENT": "tan", "ARCSINE": "asin", "ARCCOSINE": "acos",
            "ARCTANGENT": "atan", "ARCTAN2": "atan2", "SINH": "sinh", "COSH": "cosh", "TANH": "tanh",
            "RADIANS": "radians", "DEGREES": "degrees", "MULTIPLY_ADD": "multiplyadd"}
_MATH_ARITY = {"SQRT": 1, "INVERSE_SQRT": 1, "ABSOLUTE": 1, "EXPONENT": 1, "SIGN": 1, "ROUND": 1, "FLOOR": 1,
               "CEIL": 1, "TRUNC": 1, "FRACT": 1, "SINE": 1, "COSINE": 1, "TANGENT": 1, "ARCSINE": 1,
               "ARCCOSINE": 1, "ARCTANGENT": 1, "SINH": 1, "COSH": 1, "TANH": 1, "RADIANS": 1, "DEGREES": 1,
               "WRAP": 3, "SMOOTH_MIN": 3, "SMOOTH_MAX": 3, "MULTIPLY_ADD": 3, "COMPARE": 3}
_VMATH_FN = {"CROSS_PRODUCT": "cross", "PROJECT": "project", "REFLECT": "reflect", "DOT_PRODUCT": "dot",
             "DISTANCE": "distance", "LENGTH": "length", "NORMALIZE": "normalize", "ABSOLUTE": "vabs",
             "MINIMUM": "vmin", "MAXIMUM": "vmax", "FLOOR": "vfloor", "CEIL": "vceil", "FRACTION": "vfract",
             "MODULO": "vmod", "WRAP": "vwrap", "SNAP": "vsnap", "SINE": "vsin", "COSINE": "vcos",
             "TANGENT": "vtan", "FACEFORWARD": "faceforward", "REFRACT": "refract"}
_VMATH_ARITY = {"LENGTH": 1, "NORMALIZE": 1, "ABSOLUTE": 1, "FLOOR": 1, "CEIL": 1, "FRACTION": 1, "SINE": 1,
                "COSINE": 1, "TANGENT": 1, "WRAP": 3, "FACEFORWARD": 3, "MULTIPLY_ADD": 3}
_CMP = {"LESS_THAN": "<", "LESS_EQUAL": "<=", "GREATER_THAN": ">", "GREATER_EQUAL": ">=", "EQUAL": "==",
        "NOT_EQUAL": "!="}
_PREFIX = {"FLOAT": "f", "INT": "i", "BOOLEAN": "b", "FLOAT_VECTOR": "v", "FLOAT_COLOR": "c", "BYTE_COLOR": "c",
           "QUATERNION": "p", "FLOAT4X4": "4", "STRING": "s", "FLOAT2": "u", "INT8": "i"}
_SOCK_T = {"VALUE": "float", "INT": "int", "BOOLEAN": "bool", "VECTOR": "vector", "RGBA": "vector",
           "ROTATION": "rotation", "MATRIX": "matrix", "STRING": "string"}
_DECL = {"float": "float", "int": "int", "bool": "int", "vector": "vector", "rotation": "vector4",
         "matrix": "matrix", "string": "string"}
_DOMAIN_WORD = {"POINT": "point", "FACE": "prim", "CORNER": "vertex", "EDGE": "edge", "CURVE": "curve",
                "INSTANCE": "instance", "LAYER": "layer"}
_IFACE_PARAM = {"NodeSocketFloat": ("chf", "float"), "NodeSocketInt": ("chi", "int"),
                "NodeSocketBool": ("chb", "bool"), "NodeSocketVector": ("chv", "vector"),
                "NodeSocketString": ("chs", "string"), "NodeSocketRotation": ("chp", "rotation"),
                "NodeSocketObject": ("chobj", "object"), "NodeSocketCollection": ("chcoll", "collection"),
                "NodeSocketMaterial": ("chmat", "material"), "NodeSocketImage": ("chimg", "image"),
                "NodeSocketSound": ("chsound", "sound"), "NodeSocketColor": ("chv", "vector")}


def num(x):
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float):
        if math.isinf(x) or math.isnan(x):
            return "0"
        if x == int(x) and abs(x) < 1e15:
            return str(int(x))
        r = f"{x:.6g}"
        return r
    return str(x)


def _vec(v):
    return "{" + ", ".join(num(float(x)) for x in list(v)[:3]) + "}"


class _Unsupported(Exception):
    pass


class Decompiler:
    def __init__(self, data):
        self.data = data
        self.nodes = data["nodes"]
        self.iface = {it["identifier"]: it for it in data["interface"]}
        self.lines = []
        self.notes = []
        self.indent = 0
        self.locals = {}           # (node, socket) → local variable name
        self.names = set()
        self.domain = "POINT"
        self.reserved = {}         # (node, socket) → deltatime / iteration / elemindex
        self._noted = set()
        self.header = []           # declarations that go before everything (constant arrays)
        self.lists = {}            # Field to List node → array variable

    # ── helpers ────────────────────────────────────────────────────────────
    def note(self, msg):
        if msg not in self._noted:
            self._noted.add(msg)
            self.notes.append(msg)

    def emit(self, text):
        self.lines.append("    " * self.indent + text)

    def inp(self, node, key):
        for s in node["inputs"]:
            if s["id"] == key and s["enabled"]:
                return s
        for s in node["inputs"]:
            if s["name"] == key and s["enabled"]:
                return s
        for s in node["inputs"]:
            if s["id"] == key or s["name"] == key:
                return s
        return None

    def link(self, node, key):
        s = self.inp(node, key)
        return s["links"][0] if s and s["links"] else None

    def fresh(self, base):
        base = "".join(c if c.isalnum() or c == "_" else "_" for c in (base or "v").lower()) or "v"
        if base[0].isdigit():
            base = "v_" + base
        name, k = base, 2
        while name in self.names or name in ("float", "int", "vector", "if", "for", "return", "string"):
            name = f"{base}{k}"
            k += 1
        self.names.add(name)
        return name

    # ── expressions: (text, type, precedence) ─────────────────────────────
    def value(self, node, key, want="float"):
        """Expression for an input socket (linked or its default)."""
        s = self.inp(node, key)
        if s is None:
            raise _Unsupported(f"missing input {key}")
        if s["links"]:
            fn, fs = s["links"][0]
            return self.expr(fn, fs)
        return self.const(s, want)

    def const(self, s, want="float"):
        v = s["value"]
        t = _SOCK_T.get(s["type"], want)
        if s.get("hide_value") and not s["links"]:
            return self.implicit(s)
        if s["type"] == "VECTOR" and isinstance(v, list):
            return _vec(v), "vector", ATOM
        if s["type"] == "RGBA" and isinstance(v, list):
            return _vec(v[:3]), "vector", ATOM
        if s["type"] == "ROTATION" and isinstance(v, list):
            if all(abs(x) < 1e-9 for x in v):
                return "{0, 0, 0, 1}", "rotation", ATOM
            return f"euler({_vec(v)})", "rotation", ATOM
        if s["type"] == "STRING":
            return '"' + str(v or "").replace("\\", "\\\\").replace('"', '\\"') + '"', "string", ATOM
        if s["type"] == "MENU":
            return f'"{v}"', "string", ATOM
        if v is None:
            return ("0", t, ATOM) if t != "vector" else ("{0, 0, 0}", t, ATOM)
        if isinstance(v, (int, float, bool)):
            text = num(v)
            return text, t, ATOM if not text.startswith("-") else UNARY
        return "0", t, ATOM

    def implicit(self, s):
        name = s["name"]
        if name in ("Position", "Vector"):
            return "v@P", "vector", ATOM
        if name == "Normal":
            return "v@N", "vector", ATOM
        if name == "ID":
            return "@id", "int", ATOM
        if name == "Index":
            return self.index_name(), "int", ATOM
        if name == "Selection":
            return "1", "bool", ATOM
        self.note(f"implicit input '{name}' used its default")
        return "0", _SOCK_T.get(s["type"], "float"), ATOM

    def index_name(self):
        return {"POINT": "@ptnum", "FACE": "@primnum", "CORNER": "@vtxnum"}.get(self.domain, "@elemnum")

    def wrap(self, e, prec):
        text, _t, p = e
        return text if p >= prec else f"({text})"

    def call(self, fn, *args, t="float"):
        return f"{fn}({', '.join(a[0] for a in args)})", t, ATOM

    def binop(self, a, op, b, prec, t):
        right_prec = prec + 1 if op in ("-", "/", "%") else prec
        return f"{self.wrap(a, prec)} {op} {self.wrap(b, right_prec)}", t, prec

    def expr(self, node_name, sock):
        key = (node_name, sock)
        if key in self.locals:
            name, t = self.locals[key]
            return name, t, ATOM
        if key in self.reserved:
            return self.reserved[key], "float" if self.reserved[key] == "deltatime" else "int", ATOM
        node = self.nodes[node_name]
        idn = node["idname"]
        fn = getattr(self, "x_" + idn, None)
        if fn is None:
            self.note(f"no script equivalent for '{node['label'] or node_name}' ({idn}) — replaced by 0")
            out = next((o for o in node["outputs"] if o["id"] == sock), None)
            t = _SOCK_T.get(out["type"], "float") if out else "float"
            return ("{0, 0, 0}" if t == "vector" else "0"), t, ATOM
        try:
            return fn(node, sock)
        except _Unsupported as e:
            self.note(f"'{node['label'] or node_name}': {e} — replaced by 0")
            return "0", "float", ATOM

    # inputs
    def x_NodeGroupInput(self, node, sock):
        it = self.iface.get(sock)
        if it is None:
            raise _Unsupported("unknown group input")
        st = it["socket_type"]
        if st == "NodeSocketGeometry":
            raise _Unsupported("geometry used as a value")
        fn, t = _IFACE_PARAM.get(st, ("chf", "float"))
        name = (it["panel"] + "/" if it["panel"] else "") + it["name"]
        args = [f'"{name}"']
        d = it["default"]
        if fn in ("chf", "chi") and d is not None:
            args.append(num(d))
            if it["min"] is not None and it["min"] > -1e8:
                args.append(f"min={num(it['min'])}")
            if it["max"] is not None and it["max"] < 1e8:
                args.append(f"max={num(it['max'])}")
        elif fn == "chb" and d is not None:
            args.append("true" if d else "false")
        elif fn == "chv" and isinstance(d, list):
            args.append(_vec(d[:3]))
        elif fn == "chs" and d:
            args.append('"' + str(d) + '"')
        if it.get("description"):
            args.append(f'tip="{it["description"]}"')
        return f"{fn}({', '.join(args)})", t, ATOM

    def x_ShaderNodeValue(self, node, sock):
        v = node["outputs"][0]["value"]
        return num(float(v or 0.0)), "float", ATOM if (v or 0) >= 0 else UNARY

    def x_FunctionNodeInputInt(self, node, sock):
        return num(int(node["props"].get("integer", 0))), "int", ATOM

    def x_FunctionNodeInputBool(self, node, sock):
        return ("true" if node["props"].get("boolean") else "false"), "bool", ATOM

    def x_FunctionNodeInputVector(self, node, sock):
        return _vec(node["props"].get("vector", (0, 0, 0))), "vector", ATOM

    def x_FunctionNodeInputColor(self, node, sock):
        return _vec(node["props"].get("value", (0, 0, 0, 1))[:3]), "vector", ATOM

    def x_FunctionNodeInputString(self, node, sock):
        return '"' + str(node["props"].get("string", "")) + '"', "string", ATOM

    def x_FunctionNodeInputRotation(self, node, sock):
        return f"euler({_vec(node['props'].get('rotation_euler', (0, 0, 0)))})", "rotation", ATOM

    def x_GeometryNodeInputPosition(self, node, sock):
        return "v@P", "vector", ATOM

    def x_GeometryNodeInputNormal(self, node, sock):
        return "v@N", "vector", ATOM

    def x_GeometryNodeInputIndex(self, node, sock):
        return self.index_name(), "int", ATOM

    def x_GeometryNodeInputID(self, node, sock):
        return "@id", "int", ATOM

    def x_GeometryNodeInputRadius(self, node, sock):
        return "@pscale", "float", ATOM

    def x_GeometryNodeInputCurveTilt(self, node, sock):
        return "f@tilt", "float", ATOM

    def x_GeometryNodeInputTangent(self, node, sock):
        return "v@tangent", "vector", ATOM

    def x_GeometryNodeInputMeshFaceArea(self, node, sock):
        return "@area", "float", ATOM

    def x_GeometryNodeInputMeshIsland(self, node, sock):
        return ("@island" if sock == "Island Index" else "@numislands"), "int", ATOM

    def x_GeometryNodeInputMeshEdgeAngle(self, node, sock):
        return ("@edgeangle" if sock == "Unsigned Angle" else "@signededgeangle"), "float", ATOM

    def x_GeometryNodeInputMaterialIndex(self, node, sock):
        return "@material_index", "int", ATOM

    def x_GeometryNodeInputShadeSmooth(self, node, sock):
        return "@shadesmooth", "bool", ATOM

    def x_GeometryNodeSplineParameter(self, node, sock):
        return {"Factor": "@curveparam", "Length": "@curvelength"}.get(sock, "@curveparam"), "float", ATOM

    def x_GeometryNodeInputSplineCyclic(self, node, sock):
        return "@is_cyclic", "bool", ATOM

    def x_GeometryNodeInputSceneTime(self, node, sock):
        return ("@Frame" if sock == "Frame" else "@Time"), "float", ATOM

    def x_GeometryNodeInputInstanceRotation(self, node, sock):
        return "p@orient", "rotation", ATOM

    def x_GeometryNodeInputInstanceScale(self, node, sock):
        return "v@scale", "vector", ATOM

    def x_GeometryNodeInputNamedAttribute(self, node, sock):
        name = self.inp(node, "Name")
        if name["links"]:
            raise _Unsupported("attribute name comes from a link")
        dt = node["props"].get("data_type", "FLOAT")
        if sock == "Exists":
            return f'haspointattrib(0, "{name["value"]}")', "bool", ATOM
        prefix = _PREFIX.get(dt, "f")
        attr = name["value"]
        if attr == "UVMap":
            return "v@uv", "vector", ATOM
        t = {"f": "float", "i": "int", "b": "bool", "v": "vector", "c": "vector", "p": "rotation",
             "4": "matrix", "s": "string", "u": "vector"}[prefix]
        return f"{prefix}@{attr}", t, ATOM

    # math
    def x_ShaderNodeMath(self, node, sock):
        op = node["props"].get("operation", "ADD")
        a = self.value(node, "Value")
        n = _MATH_ARITY.get(op, 2)
        b = self.value(node, "Value_001") if n >= 2 else None
        c = self.value(node, "Value_002") if n >= 3 else None
        if op in _MATH_BIN:
            sym, prec = _MATH_BIN[op]
            if op == "MULTIPLY" and b[0] == "-1":
                e = (f"-{self.wrap(a, UNARY)}", "float", UNARY)
            else:
                e = self.binop(a, sym, b, prec, "float")
        elif op == "POWER":
            e = self.call("pow", a, b)
        elif op == "LOGARITHM":
            if _isnum(b[0]) and abs(float(b[0]) - math.e) < 1e-5:
                e = self.call("log", a)
            else:
                e = self.call("log", a, b)
        elif op == "COMPARE":
            e = (f"abs({a[0]} - {b[0]}) <= {c[0]} ? 1 : 0", "float", TERN)
        elif op in _MATH_FN:
            args = [x for x in (a, b, c) if x is not None]
            e = self.call(_MATH_FN[op], *args)
        else:
            raise _Unsupported(f"math operation {op}")
        if node["props"].get("use_clamp"):
            e = self.call("clamp", e, ("0", "float", ATOM), ("1", "float", ATOM))
        return e

    def x_FunctionNodeIntegerMath(self, node, sock):
        op = node["props"].get("operation", "ADD")
        a = self.value(node, "Value")
        b = self.value(node, "Value_001")
        if op in _MATH_BIN:
            sym, prec = _MATH_BIN[op]
            return self.binop(a, sym, b, prec, "int")
        if op == "MODULO":
            return self.binop(a, "%", b, MUL, "int")
        if op in ("MINIMUM", "MAXIMUM"):
            return self.call(op[:3].lower(), a, b, t="int")
        if op == "ABSOLUTE":
            return self.call("abs", a, t="int")
        if op == "NEGATE":
            return f"-{self.wrap(a, UNARY)}", "int", UNARY
        if op == "POWER":
            return self.call("pow", a, b, t="int")
        if op == "DIVIDE_FLOOR":
            return self.call("floor", (f"{self.wrap(a, MUL)} / {self.wrap(b, MUL + 1)}", "float", MUL), t="int")
        raise _Unsupported(f"integer operation {op}")

    def x_ShaderNodeVectorMath(self, node, sock):
        op = node["props"].get("operation", "ADD")
        a = self.value(node, "Vector", "vector")
        n = _VMATH_ARITY.get(op, 2)
        if op == "SCALE":
            s = self.value(node, "Scale")
            return self.binop(a, "*", s, MUL, "vector")
        if op in ("ADD", "SUBTRACT", "MULTIPLY", "DIVIDE"):
            b = self.value(node, "Vector_001", "vector")
            sym, prec = _MATH_BIN[op]
            return self.binop(a, sym, b, prec, "vector")
        if op == "MULTIPLY_ADD":
            b = self.value(node, "Vector_001", "vector")
            c = self.value(node, "Vector_002", "vector")
            return self.binop(self.binop(a, "*", b, MUL, "vector"), "+", c, ADD, "vector")
        if op == "REFRACT":
            b = self.value(node, "Vector_001", "vector")
            return self.call("refract", a, b, self.value(node, "Scale"), t="vector")
        args = [a]
        if n >= 2:
            args.append(self.value(node, "Vector_001", "vector"))
        if n >= 3:
            args.append(self.value(node, "Vector_002", "vector"))
        if op not in _VMATH_FN:
            raise _Unsupported(f"vector operation {op}")
        t = "float" if op in ("DOT_PRODUCT", "DISTANCE", "LENGTH") else "vector"
        return self.call(_VMATH_FN[op], *args, t=t)

    def x_ShaderNodeMapRange(self, node, sock):
        p = node["props"]
        vec = p.get("data_type") == "FLOAT_VECTOR"
        if vec:
            keys = ("Vector", "From_Min_FLOAT3", "From_Max_FLOAT3", "To_Min_FLOAT3", "To_Max_FLOAT3")
            vals = [self.value(node, k, "vector") for k in keys]
        else:
            keys = ("Value", "From Min", "From Max", "To Min", "To Max")
            vals = [self.value(node, k) for k in keys]
        t = "vector" if vec else "float"
        interp = p.get("interpolation_type", "LINEAR")
        if interp in ("SMOOTHSTEP", "SMOOTHERSTEP") and not vec:
            fn = "smoothstep" if interp == "SMOOTHSTEP" else "smootherstep"
            e = self.call(fn, vals[1], vals[2], vals[0])
            if vals[3][0] == "0" and vals[4][0] == "1":
                return e
            return self.call("fit01", e, vals[3], vals[4])
        if interp == "STEPPED":
            self.note("Map Range 'Stepped' became a linear fit")
        fn = "fit" if p.get("clamp", True) else "efit"
        return self.call(fn, *vals, t=t)

    def x_ShaderNodeClamp(self, node, sock):
        return self.call("clamp", self.value(node, "Value"), self.value(node, "Min"), self.value(node, "Max"))

    def x_ShaderNodeMix(self, node, sock):
        p = node["props"]
        dt = p.get("data_type", "FLOAT")
        f = self.value(node, "Factor_Float")
        if dt == "FLOAT":
            return self.call("lerp", self.value(node, "A_Float"), self.value(node, "B_Float"), f)
        if dt == "VECTOR":
            return self.call("lerp", self.value(node, "A_Vector", "vector"), self.value(node, "B_Vector", "vector"),
                             f, t="vector")
        if dt == "ROTATION":
            return self.call("slerp", self.value(node, "A_Rotation"), self.value(node, "B_Rotation"), f,
                             t="rotation")
        mode = p.get("blend_type", "MIX").lower().replace("_", "")
        a, b = self.value(node, "A_Color", "vector"), self.value(node, "B_Color", "vector")
        if mode == "mix":
            return self.call("lerp", a, b, f, t="vector")
        return self.call("colormix", a, b, f, (f'"{mode}"', "string", ATOM), t="vector")

    def x_ShaderNodeCombineXYZ(self, node, sock):
        parts = [self.value(node, k) for k in "XYZ"]
        if all(_isnum(p[0]) for p in parts):
            return "{" + ", ".join(p[0] for p in parts) + "}", "vector", ATOM
        return self.call("set", *parts, t="vector")

    def x_ShaderNodeSeparateXYZ(self, node, sock):
        v = self.value(node, "Vector", "vector")
        return f"{self.wrap(v, ATOM)}.{sock.lower()}", "float", ATOM

    def x_FunctionNodeCompare(self, node, sock):
        p = node["props"]
        op = p.get("operation", "GREATER_THAN")
        dt = p.get("data_type", "FLOAT")
        if dt == "VECTOR" and p.get("mode", "ELEMENT") != "ELEMENT":
            raise _Unsupported(f"vector comparison mode {p.get('mode')}")
        if op not in _CMP:
            raise _Unsupported(f"comparison {op}")
        want = "vector" if dt == "VECTOR" else "float"
        a, b = self.value(node, "A", want), self.value(node, "B", want)
        return self.binop(a, _CMP[op], b, CMP, "bool")

    def x_FunctionNodeBooleanMath(self, node, sock):
        op = node["props"].get("operation", "AND")
        a = self.value(node, "Boolean", "bool")
        if op == "NOT":
            return f"!{self.wrap(a, UNARY)}", "bool", UNARY
        b = self.value(node, "Boolean_001", "bool")
        if op == "AND":
            return self.binop(a, "&&", b, AND, "bool")
        if op == "OR":
            return self.binop(a, "||", b, OR, "bool")
        if op == "NAND":
            return f"!({a[0]} && {b[0]})", "bool", UNARY
        if op == "NOR":
            return f"!({a[0]} || {b[0]})", "bool", UNARY
        if op == "XOR":
            return self.binop(a, "!=", b, CMP, "bool")
        if op == "XNOR":
            return self.binop(a, "==", b, CMP, "bool")
        if op == "IMPLY":
            return f"!{self.wrap(a, UNARY)} || {self.wrap(b, OR)}", "bool", OR
        if op == "NIMPLY":
            return f"{self.wrap(a, AND)} && !{self.wrap(b, UNARY)}", "bool", AND
        raise _Unsupported(f"boolean operation {op}")

    def x_GeometryNodeSwitch(self, node, sock):
        c = self.value(node, "Switch", "bool")
        f = self.value(node, "False")
        t = self.value(node, "True")
        return f"{self.wrap(c, TERN + 1)} ? {self.wrap(t, TERN + 1)} : {self.wrap(f, TERN)}", t[1], TERN

    def x_GeometryNodeIndexSwitch(self, node, sock):
        idx = self.value(node, "Index", "int")
        items = [s for s in node["inputs"] if s["id"].startswith("Item_") and s["enabled"]]
        if not items:
            raise _Unsupported("empty index switch")
        vals = [self.value(node, s["id"]) for s in items]
        if all(v[1] == "float" for v in vals) and len(vals) == 3:
            return self.call("getcomp", self.call("set", *vals, t="vector"), idx)
        out = vals[-1]
        for k in range(len(vals) - 2, -1, -1):
            out = (f"{idx[0]} == {k} ? {self.wrap(vals[k], TERN + 1)} : {self.wrap(out, TERN)}", vals[k][1], TERN)
        return out

    # textures & random
    def x_ShaderNodeTexNoise(self, node, sock):
        p = node["props"]
        dims = p.get("noise_dimensions", "3D")
        args = []
        if dims == "1D":
            args.append(self.value(node, "W"))
        elif dims == "4D":
            pos = self.value(node, "Vector", "vector")
            return self.call("flownoise", pos, self.value(node, "W"), self.value(node, "Scale"),
                             t="vector" if sock == "Color" else "float")
        else:
            args.append(self.value(node, "Vector", "vector"))
        args += [self.value(node, "Scale"), self.value(node, "Detail"), self.value(node, "Roughness")]
        while len(args) > 1 and args[-1][0] in ({3: "0.5", 2: "2", 1: "5"}.get(len(args) - 1),):
            args.pop()
        for key, default in (("Lacunarity", 2.0), ("Distortion", 0.0)):
            s = self.inp(node, key)
            if s and (s["links"] or abs(float(s["value"] or 0) - default) > 1e-6):
                self.note(f"Noise Texture's {key} isn't expressible — the default is used")
        ntype = p.get("noise_type", "FBM")
        fn = "vnoise" if sock == "Color" else "noise"
        if ntype == "RIDGED_MULTIFRACTAL" and fn == "noise":
            fn = "ridged"
        return self.call(fn, *args, t="vector" if sock == "Color" else "float")

    def x_ShaderNodeTexVoronoi(self, node, sock):
        p = node["props"]
        feat = p.get("feature", "F1")
        args = [self.value(node, "Vector", "vector"), self.value(node, "Scale"), self.value(node, "Randomness")]
        if args[-1][0] == "1":
            args.pop()
            if args[-1][0] == "5":
                args.pop()
        if feat == "DISTANCE_TO_EDGE":
            return self.call("worleyedge", *args)
        if sock == "Position":
            return self.call("voronoi", *args, t="vector")
        if sock == "Color":
            r = self.call("cellrand", *args)
            return f"set({r[0]}, {r[0]}, {r[0]})", "vector", ATOM
        return self.call("worley", *args)

    def x_ShaderNodeTexGabor(self, node, sock):
        args = [self.value(node, "Vector", "vector"), self.value(node, "Scale"), self.value(node, "Frequency"),
                self.value(node, "Anisotropy")]
        return self.call("gabor", *args)

    def x_FunctionNodeRandomValue(self, node, sock):
        p = node["props"]
        dt = p.get("data_type", "FLOAT")
        idl = self.inp(node, "ID")
        seed = self.value(node, "Seed", "int")
        if dt not in ("FLOAT", "FLOAT_VECTOR", "INT"):
            raise _Unsupported(f"random {dt.lower()} values")
        lo = self.value(node, "Min", "vector" if dt == "FLOAT_VECTOR" else "float")
        hi = self.value(node, "Max", "vector" if dt == "FLOAT_VECTOR" else "float")
        if idl["links"] or seed[0] == "0":
            idv = self.value(node, "ID", "int") if idl["links"] else ("@id", "int", ATOM)
            if seed[0] != "0":
                idv = self.binop(idv, "+", self.binop(seed, "*", ("7919", "int", ATOM), MUL, "int"), ADD, "int")
                self.note("Random Value's seed was folded into the id — values change")
            r = self.call("rand", idv, t="vector" if dt == "FLOAT_VECTOR" else "float")
            if dt == "FLOAT_VECTOR":
                r = self.call("vector", r, t="vector")
                return self.call("fit01", r, lo, hi, t="vector") if (lo[0], hi[0]) != ("{0, 0, 0}", "{1, 1, 1}") \
                    else r
            if (lo[0], hi[0]) == ("0", "1"):
                return r
            r = self.call("fit01", r, lo, hi)
            if dt == "INT":
                return self.call("int", r, t="int")
            return r
        return self.call("random", lo, hi, seed)

    def x_FunctionNodeHashValue(self, node, sock):
        return self.call("hash", self.value(node, "Value"), self.value(node, "Seed", "int"), t="int")

    def x_ShaderNodeVectorRotate(self, node, sock):
        p = node["props"]
        v = self.value(node, "Vector", "vector")
        center = self.value(node, "Center", "vector")
        angle = self.value(node, "Angle")
        if p.get("invert"):
            angle = (f"-{self.wrap(angle, UNARY)}", "float", UNARY)
        rt = p.get("rotation_type", "AXIS_ANGLE")
        if rt == "AXIS_ANGLE":
            axis = self.value(node, "Axis", "vector")
        elif rt in ("X_AXIS", "Y_AXIS", "Z_AXIS"):
            axis = ({"X_AXIS": "{1, 0, 0}", "Y_AXIS": "{0, 1, 0}", "Z_AXIS": "{0, 0, 1}"}[rt], "vector", ATOM)
        else:
            rot = self.value(node, "Rotation", "vector")
            pv = v if center[0] == "{0, 0, 0}" else self.binop(v, "-", center, ADD, "vector")
            e = self.call("qrotate", self.call("euler", rot, t="rotation"), pv, t="vector")
            return e if center[0] == "{0, 0, 0}" else self.binop(e, "+", center, ADD, "vector")
        if center[0] == "{0, 0, 0}":
            return self.call("rotate", v, axis, angle, t="vector")
        e = self.call("rotate", self.binop(v, "-", center, ADD, "vector"), axis, angle, t="vector")
        return self.binop(e, "+", center, ADD, "vector")

    # rotation / matrix / colour
    def x_FunctionNodeAxisAngleToRotation(self, node, sock):
        return self.call("quaternion", self.value(node, "Angle"), self.value(node, "Axis", "vector"), t="rotation")

    def x_FunctionNodeEulerToRotation(self, node, sock):
        return self.call("euler", self.value(node, "Euler", "vector"), t="rotation")

    def x_FunctionNodeRotationToEuler(self, node, sock):
        return self.call("quaterniontoeuler", self.value(node, "Rotation"), t="vector")

    def x_FunctionNodeRotateVector(self, node, sock):
        return self.call("qrotate", self.value(node, "Rotation"), self.value(node, "Vector", "vector"), t="vector")

    def x_FunctionNodeRotateRotation(self, node, sock):
        a, b = self.value(node, "Rotation"), self.value(node, "Rotate By")
        if node["props"].get("rotation_space", "GLOBAL") == "GLOBAL":
            return self.call("qmultiply", b, a, t="rotation")
        return self.call("qmultiply", a, b, t="rotation")

    def x_FunctionNodeInvertRotation(self, node, sock):
        return self.call("qinvert", self.value(node, "Rotation"), t="rotation")

    def x_FunctionNodeQuaternionToRotation(self, node, sock):
        parts = [self.value(node, k) for k in ("X", "Y", "Z", "W")]
        return self.call("set", *parts, t="rotation")

    def x_FunctionNodeRotationToQuaternion(self, node, sock):
        r = self.value(node, "Rotation")
        return f"{self.wrap(r, ATOM)}.{sock.lower()}", "float", ATOM

    def x_FunctionNodeAlignRotationToVector(self, node, sock):
        rot = self.inp(node, "Rotation")
        if rot["links"] or any(abs(x) > 1e-9 for x in (rot["value"] or [0, 0, 0])):
            raise _Unsupported("aligning a non-identity rotation")
        p = node["props"]
        f = self.inp(node, "Factor")
        if f["links"] or abs(float(f["value"] if f["value"] is not None else 1.0) - 1.0) > 1e-9 \
                or p.get("pivot_axis", "AUTO") != "AUTO":
            self.note("Align Rotation to Vector's factor / pivot axis became a full, automatic alignment")
        v = self.value(node, "Vector", "vector")
        axis = p.get("axis", "Z")
        if axis == "Z":
            return self.call("alignaxis", v, t="rotation")
        return self.call("alignaxis", v, (f'"{axis.lower()}"', "string", ATOM), t="rotation")

    def x_FunctionNodeAxesToRotation(self, node, sock):
        p = node["props"]
        axis, second = p.get("primary_axis", "Z"), p.get("secondary_axis", "Y")
        prim = self.value(node, "Primary Axis", "vector")
        sec = self.value(node, "Secondary Axis", "vector")
        if second != ("Y" if axis != "Y" else "Z"):
            raise _Unsupported(f"axes {axis}/{second}")
        if axis == "Z":
            return self.call("alignaxis", prim, sec, t="rotation")
        return self.call("alignaxis", prim, sec, (f'"{axis.lower()}"', "string", ATOM), t="rotation")

    def x_FunctionNodeCombineTransform(self, node, sock):
        return self.call("maketransform", self.value(node, "Translation", "vector"), self.value(node, "Rotation"),
                         self.value(node, "Scale", "vector"), t="matrix")

    def x_FunctionNodeSeparateTransform(self, node, sock):
        fn = {"Translation": "gettranslation", "Rotation": "getrotation", "Scale": "getscale"}[sock]
        return self.call(fn, self.value(node, "Transform"), t="rotation" if sock == "Rotation" else "vector")

    def x_FunctionNodeTransformPoint(self, node, sock):
        return self.binop(self.value(node, "Vector", "vector"), "*", self.value(node, "Transform"), MUL, "vector")

    def x_FunctionNodeTransformDirection(self, node, sock):
        return self.call("transformdir", self.value(node, "Direction", "vector"), self.value(node, "Transform"),
                         t="vector")

    def x_FunctionNodeInvertMatrix(self, node, sock):
        return self.call("invert", self.value(node, "Matrix"), t="matrix")

    def x_FunctionNodeMatrixMultiply(self, node, sock):
        return self.binop(self.value(node, "Matrix_001"), "*", self.value(node, "Matrix"), MUL, "matrix")

    def x_ShaderNodeBlackbody(self, node, sock):
        return self.call("blackbody", self.value(node, "Temperature"), t="vector")

    def x_FunctionNodeCombineColor(self, node, sock):
        mode = node["props"].get("mode", "RGB")
        parts = [self.value(node, k) for k in ("Red", "Green", "Blue")]
        v = self.call("set", *parts, t="vector")
        if mode == "HSV":
            return self.call("hsvtorgb", v, t="vector")
        if mode == "HSL":
            return self.call("hsltorgb", v, t="vector")
        return v

    def x_FunctionNodeSeparateColor(self, node, sock):
        mode = node["props"].get("mode", "RGB")
        c = self.value(node, "Color", "vector")
        if mode == "HSV":
            c = self.call("rgbtohsv", c, t="vector")
        elif mode == "HSL":
            c = self.call("rgbtohsl", c, t="vector")
        comp = {"Red": "x", "Green": "y", "Blue": "z"}.get(sock)
        if comp is None:
            return "1", "float", ATOM
        return f"{self.wrap(c, ATOM)}.{comp}", "float", ATOM

    # ramps
    def _ramp_args(self, node, default):
        name = node["extra"].get("ramp") or node["label"].replace("Ramp: ", "") or default
        preset = node["extra"].get("preset") or ""
        if not node["extra"].get("ramp"):
            self.note(f"ramp '{name}' starts from its default shape — the curve isn't copied when converting "
                      f"into a new group")
        return [(_quote(name), "string", ATOM)], ([(_quote(preset), "string", ATOM)] if preset else [])

    def x_ShaderNodeFloatCurve(self, node, sock):
        head, tail = self._ramp_args(node, "curve")
        return self.call("chramp", *head, self.value(node, "Value"), *tail)

    def x_ShaderNodeValToRGB(self, node, sock):
        if sock == "Alpha":
            raise _Unsupported("a colour ramp's alpha")
        head, tail = self._ramp_args(node, "colors")
        return self.call("colorramp", *head, self.value(node, "Fac"), *tail, t="vector")

    # geometry lookups
    def _geometry_ref(self, node, key):
        """'0' when the geometry input is the main chain, 'chobj(...)' for Object Info, else unsupported."""
        lk = self.link(node, key)
        if lk is None:
            raise _Unsupported("geometry input isn't connected")
        src = self.nodes[lk[0]]
        if src["idname"] == "GeometryNodeObjectInfo":
            obj = self.link(src, "Object")
            if obj and self.nodes[obj[0]]["idname"] == "NodeGroupInput":
                it = self.iface.get(obj[1])
                if it and it["name"].startswith("Input ") and it["name"][6:].isdigit():
                    return it["name"][6:]
                return f'chobj("{it["name"] if it else "object"}")'
            raise _Unsupported("object chosen inside the tree")
        return "0"

    def x_GeometryNodeSampleIndex(self, node, sock):
        g = self._geometry_ref(node, "Geometry")
        dom = node["props"].get("domain", "POINT")
        fn = {"POINT": "point", "FACE": "prim", "CORNER": "vertex"}.get(dom)
        idx = self.value(node, "Index", "int")
        val = self.inp(node, "Value")
        t = _SOCK_T.get(next((o["type"] for o in node["outputs"] if o["id"] == "Value"), "VALUE"), "float")
        if fn and val["links"]:
            src = self.nodes[val["links"][0][0]]
            name = None
            if src["idname"] == "GeometryNodeInputPosition":
                name = "P"
            elif src["idname"] == "GeometryNodeInputNamedAttribute" and not self.inp(src, "Name")["links"]:
                pre = _PREFIX.get(src["props"].get("data_type", "FLOAT"), "f")
                name = f"{pre}@{self.inp(src, 'Name')['value']}"
            elif src["idname"] == "GeometryNodeInputNormal":
                name = "N"
            if name:
                return self.call(fn, (g, "int", ATOM), (f'"{name}"', "string", ATOM), idx, t=t)
        if g == "0":
            saved = self.domain
            self.domain = dom
            v = self.value(node, "Value")
            self.domain = saved
            return self.call("atindex", v, idx, t=v[1])
        raise _Unsupported("sampling an expression on another geometry")

    def x_GeometryNodeFieldAtIndex(self, node, sock):
        return self.call("atindex", self.value(node, "Value"), self.value(node, "Index", "int"))

    def x_GeometryNodeFieldOnDomain(self, node, sock):
        dom = _DOMAIN_WORD.get(node["props"].get("domain", "POINT"), "point")
        return self.call("ondomain", self.value(node, "Value"), (f'"{dom}"', "string", ATOM))

    def x_GeometryNodeBlurAttribute(self, node, sock):
        return self.call("blur", self.value(node, "Value"), self.value(node, "Iterations", "int"),
                         self.value(node, "Weight"))

    def x_GeometryNodeAccumulateField(self, node, sock):
        g = self.inp(node, "Group Index")
        args = [self.value(node, "Value")] + ([self.value(node, "Group Index", "int")] if g["links"] else [])
        if sock == "Total":
            return self.call("sumof", *args)
        if sock == "Trailing":
            return self.binop(self.call("accumulate", *args), "-", args[0], ADD, "float")
        return self.call("accumulate", *args)

    def x_GeometryNodeFieldAverage(self, node, sock):
        g = self.inp(node, "Group Index")
        args = [self.value(node, "Value")] + ([self.value(node, "Group Index", "int")] if g["links"] else [])
        return self.call("avgof" if sock == "Mean" else "medianof", *args)

    def x_GeometryNodeFieldMinAndMax(self, node, sock):
        g = self.inp(node, "Group Index")
        args = [self.value(node, "Value")] + ([self.value(node, "Group Index", "int")] if g["links"] else [])
        return self.call("minof" if sock == "Min" else "maxof", *args)

    def x_GeometryNodeFieldVariance(self, node, sock):
        g = self.inp(node, "Group Index")
        args = [self.value(node, "Value")] + ([self.value(node, "Group Index", "int")] if g["links"] else [])
        return self.call("stdevof" if sock == "Standard Deviation" else "varianceof", *args)

    def x_GeometryNodeAttributeStatistic(self, node, sock):
        gl = self.link(node, "Geometry")
        if gl and self.nodes[gl[0]]["idname"] == "GeometryNodePoints":
            a = self.link(node, "Attribute")
            while a and self.nodes[a[0]]["idname"] in ("NodeReroute",):
                a = self.link(self.nodes[a[0]], "Input")
            if a and self.nodes[a[0]]["idname"] == "GeometryNodeListGetItem":
                var, t = self.list_ref(self.link(self.nodes[a[0]], "List"))
                fn = {"Sum": "sum", "Mean": "avg", "Min": "min", "Max": "max"}.get(sock)
                if fn:
                    return f"{fn}({var})", ("vector" if t == "vector" else "float"), ATOM
            raise _Unsupported("statistics of generated points")
        g = self._geometry_ref(node, "Geometry")
        dom = node["props"].get("domain", "POINT")
        a = self.inp(node, "Attribute")
        if sock == "Sum" and not a["links"] and float(a["value"] or 0) == 1.0:
            fn = {"POINT": "npoints", "FACE": "nprimitives", "CORNER": "nvertices", "EDGE": "nedges",
                  "CURVE": "ncurves"}.get(dom)
            if fn:
                return f"{fn}({g})", "int", ATOM
        if g != "0":
            raise _Unsupported("statistics of another geometry")
        saved = self.domain
        self.domain = dom
        v = self.value(node, "Attribute")
        self.domain = saved
        fn = {"Sum": "sumof", "Mean": "avgof", "Min": "minof", "Max": "maxof", "Median": "medianof",
              "Standard Deviation": "stdevof", "Variance": "varianceof"}.get(sock)
        if fn is None:
            raise _Unsupported(f"statistic {sock}")
        if dom != saved:
            return self.call(fn, v, (f'"{_DOMAIN_WORD.get(dom, "point")}"', "string", ATOM), t=v[1])
        return self.call(fn, v, t=v[1])

    def x_GeometryNodeAttributeDomainSize(self, node, sock):
        g = self._geometry_ref(node, "Geometry")
        fn = {"Point Count": "npoints", "Edge Count": "nedges", "Face Count": "nprimitives",
              "Face Corner Count": "nvertices", "Spline Count": "ncurves"}.get(sock)
        if fn is None:
            raise _Unsupported(f"domain size output '{sock}'")
        return f"{fn}({g})", "int", ATOM

    def x_GeometryNodeBoundBox(self, node, sock):
        g = self._geometry_ref(node, "Geometry")
        fn = {"Min": "getbbox_min", "Max": "getbbox_max"}.get(sock)
        if fn is None:
            raise _Unsupported("bounding box geometry")
        return f"{fn}({g})", "vector", ATOM

    def x_GeometryNodeIndexOfNearest(self, node, sock):
        if sock != "Index":
            raise _Unsupported("Has Neighbor output")
        return self.call("nearpoint", ("0", "int", ATOM), self.value(node, "Position", "vector"), t="int")

    def x_GeometryNodeProximity(self, node, sock):
        g = self._geometry_ref(node, "Target")
        fn = {"FACES": "xyzdist", "EDGES": "edgedist", "POINTS": "pointdist"}[node["props"].get("target_element",
                                                                                              "FACES")]
        pos = self.value(node, "Source Position", "vector")
        if sock == "Position":
            if fn != "xyzdist":
                raise _Unsupported("closest position on edges/points")
            return self.call("minpos", (g, "int", ATOM), pos, t="vector")
        if sock == "Distance":
            return self.call(fn, (g, "int", ATOM), pos)
        raise _Unsupported("Is Valid output")

    def x_GeometryNodeRaycast(self, node, sock):
        g = self._geometry_ref(node, "Target Geometry")
        o = self.value(node, "Source Position", "vector")
        d = self.value(node, "Ray Direction", "vector")
        ln = self.value(node, "Ray Length")
        dirv = self.binop(self.call("normalize", d, t="vector"), "*", ln, MUL, "vector")
        fn = {"Is Hit": ("rayhit", "bool"), "Hit Position": ("raypos", "vector"),
              "Hit Normal": ("raynormal", "vector"), "Hit Distance": ("raydist", "float")}.get(sock)
        if fn is None:
            raise _Unsupported("ray-sampled attribute")
        return self.call(fn[0], (g, "int", ATOM), o, dirv, t=fn[1])

    def x_GeometryNodeObjectInfo(self, node, sock):
        obj = self.link(node, "Object")
        if not (obj and self.nodes[obj[0]]["idname"] == "NodeGroupInput"):
            raise _Unsupported("object chosen inside the tree")
        it = self.iface.get(obj[1])
        ref = f'chobj("{it["name"]}")' if it else "1"
        fn = {"Location": ("objpos", "vector"), "Rotation": ("objrot", "rotation"), "Scale": ("objscale", "vector"),
              "Transform": ("optransform", "matrix")}.get(sock)
        if fn is None:
            raise _Unsupported("object geometry used as a value")
        return f"{fn[0]}({ref})", fn[1], ATOM

    def list_ref(self, link):
        """Array variable for a list: constant arrays are declared at the top of the script."""
        name, sock = link
        if name in self.lists:
            return self.lists[name]
        node = self.nodes[name]
        if node["idname"] != "GeometryNodeFieldToList":
            raise _Unsupported("arrays built from other arrays")
        count = self.inp(node, "Count")
        if count["links"]:
            raise _Unsupported("an array with a varying length")
        n = int(count["value"] or 0)
        etype = (node["items"][0][1] if node["items"] else "FLOAT")
        t = {"FLOAT": "float", "INT": "int", "BOOLEAN": "int", "VECTOR": "vector", "RGBA": "vector",
             "ROTATION": "vector4", "STRING": "string"}.get(etype)
        if t is None:
            raise _Unsupported(f"arrays of {etype.lower()}")
        fld = self.inp(node, "Field_0") or next((s for s in node["inputs"] if s["id"].startswith("Field")), None)
        saved_lines, self.lines = self.lines, []
        try:
            if fld["links"] and self.nodes[fld["links"][0][0]]["idname"] == "GeometryNodeIndexSwitch":
                sw = self.nodes[fld["links"][0][0]]
                idx = self.link(sw, "Index")
                if not (idx and self.nodes[idx[0]]["idname"] == "GeometryNodeInputIndex"):
                    raise _Unsupported("an array whose items depend on the element")
                items = [self.value(sw, s["id"])[0] for s in sw["inputs"] if s["id"].startswith("Item_")
                         and s["enabled"]][:n]
            elif not fld["links"]:
                items = [self.const(fld)[0]] * n
            else:
                raise _Unsupported("an array whose items depend on the element")
            if self.lines or any(("@" in it) for it in items):
                raise _Unsupported("an array whose items depend on the geometry")
        finally:
            self.lines = saved_lines
        var = self.fresh("values" if t != "vector" else "vectors")
        self.header.append(f"{t} {var}[] = {{{', '.join(items)}}};")
        self.lists[name] = (var, t)
        return var, t

    def x_GeometryNodeListGetItem(self, node, sock):
        lk = self.link(node, "List")
        if lk is None:
            raise _Unsupported("an empty array")
        var, t = self.list_ref(lk)
        idx = self.value(node, "Index", "int")
        return f"{var}[{idx[0]}]", ("int" if t == "int" else "rotation" if t == "vector4" else t), ATOM

    def x_GeometryNodeListLength(self, node, sock):
        lk = self.link(node, "List")
        if lk is None:
            return "0", "int", ATOM
        return f"len({self.list_ref(lk)[0]})", "int", ATOM

    def x_NodeReroute(self, node, sock):
        lk = self.link(node, "Input")
        if lk is None:
            raise _Unsupported("unconnected reroute")
        return self.expr(*lk)

    # ═══════════════════════════════════════════════════════════════════════
    #  Geometry chain
    # ═══════════════════════════════════════════════════════════════════════
    _ZONE_OUT = {"GeometryNodeSimulationOutput", "GeometryNodeRepeatOutput",
                 "GeometryNodeForeachGeometryElementOutput"}

    def chain(self, link, stop=None):
        """Geometry operations from the group input (or ``stop``) to ``link``,
        in execution order: [(node_name, out_socket)]."""
        ops = []
        seen = set()
        while link is not None:
            name, sock = link
            if name in seen:
                break
            seen.add(name)
            node = self.nodes[name]
            if stop is not None and name == stop:
                break
            if node["idname"] == "NodeGroupInput":
                break
            if node["idname"] == "NodeReroute":
                link = self.link(node, "Input")
                continue
            ops.append((name, sock))
            if node["idname"] in self._ZONE_OUT:
                zin = next((n for n, d in self.nodes.items() if d.get("pair") == name), None)
                if zin is None:
                    raise _Unsupported("zone without an input")
                gin = self._geo_input(self.nodes[zin])
                link = gin["links"][0] if gin and gin["links"] else None
                continue
            gin = self._geo_input(node)
            link = gin["links"][0] if gin and gin["links"] else None
        ops.reverse()
        return ops

    def _geo_input(self, node):
        idn = node["idname"]
        key = {"GeometryNodeInstanceOnPoints": "Points", "GeometryNodeSimulationInput": "Item_0",
               "GeometryNodeRepeatInput": "Item_0", "GeometryNodeJoinGeometry": "Geometry"}.get(idn)
        if key:
            return self.inp(node, key)
        for s in node["inputs"]:
            if s["type"] == "GEOMETRY" and s["enabled"]:
                return s
        return None

    def run(self):
        out = None
        for name, n in self.nodes.items():
            if n["idname"] == "NodeGroupOutput" and n["props"].get("is_active_output", True):
                out = name
        if out is None:
            raise ValueError("the tree has no Group Output")
        onode = self.nodes[out]
        geo_link, result = None, None
        for s in onode["inputs"]:
            if not s["links"]:
                continue
            if s["type"] == "GEOMETRY" and geo_link is None:
                geo_link = s["links"][0]
            elif s["type"] != "GEOMETRY" and result is None:
                result = s["links"][0]
        if geo_link is not None:
            self.block(self.chain(geo_link))
        if result is not None:
            self.domain = "POINT"
            e = self.expr(*result)
            self.emit(f"return {e[0]};")
        if not self.lines:
            self.emit("// nothing to convert: the tree passes the geometry through")
        return "\n".join(self.header + self.lines) + "\n", self.notes

    def block(self, ops):
        ops = [op for k, op in enumerate(ops)
               if not self._aux_store(self.nodes[op[0]], self.nodes[ops[k + 1][0]] if k + 1 < len(ops) else None)]
        i = 0
        while i < len(ops):
            name, sock = ops[i]
            node = self.nodes[name]
            dom = self.op_domain(node)
            if dom not in ("POINT", None):
                # group consecutive statements over the same non-point domain
                j = i
                while j < len(ops) and self.op_domain(self.nodes[ops[j][0]]) == dom:
                    j += 1
                self.emit(f"runover({_DOMAIN_WORD.get(dom, 'point')}) {{")
                self.indent += 1
                for k in range(i, j):
                    self.statement(*ops[k], dom)
                self.indent -= 1
                self.emit("}")
                i = j
                continue
            self.statement(name, sock, "POINT")
            i += 1

    _FACE_OPS = {"GeometryNodeExtrudeMesh", "GeometryNodeTriangulate", "GeometryNodeFlipFaces",
                 "GeometryNodeSetMaterialIndex", "GeometryNodeSetMaterial", "GeometryNodeSetShadeSmooth",
                 "GeometryNodeDistributePointsOnFaces"}

    def op_domain(self, node):
        idn = node["idname"]
        if idn in ("GeometryNodeStoreNamedAttribute", "GeometryNodeCaptureAttribute", "GeometryNodeDeleteGeometry"):
            d = node["props"].get("domain", "POINT")
            return "FACE" if (idn == "GeometryNodeDeleteGeometry" and d == "FACE") else d
        if idn == "GeometryNodeSetMeshNormal":
            return node["props"].get("domain", "POINT")
        # face operations whose selection / values vary per face run over prims
        linked = any(s["links"] and s["type"] != "GEOMETRY" and s["enabled"]
                     and self.nodes[s["links"][0][0]]["idname"] != "NodeGroupInput" for s in node["inputs"])
        if idn in self._FACE_OPS and linked:
            if idn == "GeometryNodeSetShadeSmooth" and node["props"].get("domain", "FACE") != "FACE":
                return None
            return "FACE"
        if idn == "GeometryNodeMeshBevel" and linked:
            return "EDGE"
        return None

    def _aux_store(self, node, nxt):
        """Stores that scatter(), extrude() and topoints() add by themselves."""
        if node["idname"] != "GeometryNodeStoreNamedAttribute":
            return False
        nm, val = self.inp(node, "Name"), self.link(node, "Value")
        if nm is None or nm["links"] or val is None:
            return False
        src = self.nodes[val[0]]["idname"]
        attr = nm["value"]
        if src == "GeometryNodeDistributePointsOnFaces":
            return (attr, val[1]) in (("N", "Normal"), ("orient", "Rotation"))
        if src == "GeometryNodeExtrudeMesh":
            return (attr, val[1]) in (("extrudeFront", "Top"), ("extrudeSide", "Side"))
        if src == "GeometryNodeInputNormal" and attr == "N" and nxt is not None:
            return nxt["idname"] == "GeometryNodeMeshToPoints" and nxt["props"].get("mode") == "VERTICES"
        return False

    def selection(self, node, dom):
        s = self.inp(node, "Selection")
        if s is None or not s["links"]:
            if s is not None and s["value"] is False:
                return "0"
            return None
        saved = self.domain
        self.domain = dom
        e = self.expr(*s["links"][0])
        self.domain = saved
        return e[0]

    def guarded(self, sel, text):
        if sel is None:
            self.emit(text)
        else:
            self.emit(f"if ({sel}) {text}")

    def statement(self, name, sock, dom):
        node = self.nodes[name]
        idn = node["idname"]
        self.domain = dom
        fn = getattr(self, "s_" + idn, None)
        if fn is None:
            self.note(f"'{node['label'] or name}' ({idn}) has no script equivalent — left out")
            self.emit(f"// skipped: {node['label'] or name} ({idn})")
            return
        try:
            fn(node, name, sock, dom)
        except _Unsupported as e:
            self.note(f"'{node['label'] or name}': {e} — left out")
            self.emit(f"// skipped: {node['label'] or name} ({e})")
        finally:
            self.domain = "POINT"

    # statements
    def s_GeometryNodeSetPosition(self, node, name, sock, dom):
        sel = self.selection(node, "POINT")
        pos = self.inp(node, "Position")
        off = self.inp(node, "Offset")
        has_pos = bool(pos["links"])
        has_off = bool(off["links"]) or any(abs(x) > 1e-12 for x in (off["value"] or [0, 0, 0]))
        if has_pos and has_off:
            e = self.binop(self.value(node, "Position", "vector"), "+", self.value(node, "Offset", "vector"), ADD,
                           "vector")
            self.guarded(sel, f"@P = {e[0]};")
        elif has_pos:
            self.guarded(sel, f"@P = {self.value(node, 'Position', 'vector')[0]};")
        elif has_off:
            self.guarded(sel, f"@P += {self.value(node, 'Offset', 'vector')[0]};")

    def s_GeometryNodeStoreNamedAttribute(self, node, name, sock, dom):
        nm = self.inp(node, "Name")
        if nm["links"]:
            raise _Unsupported("the attribute name comes from a link")
        attr = nm["value"]
        dt = node["props"].get("data_type", "FLOAT")
        sel = self.selection(node, dom)
        v = self.value(node, "Value", "vector" if dt in ("FLOAT_VECTOR", "FLOAT_COLOR") else "float")
        if attr == "Cd" and dt == "FLOAT_COLOR":
            target = "@Cd"
        elif attr == "UVMap":
            target = "@uv"
        elif attr == "radius" and dt == "FLOAT":
            target = "@pscale"
        else:
            target = f"{_PREFIX.get(dt, 'f')}@{attr}"
        self.guarded(sel, f"{target} = {v[0]};")

    def s_GeometryNodeSetID(self, node, name, sock, dom):
        self.guarded(self.selection(node, "POINT"), f"@id = {self.value(node, 'ID', 'int')[0]};")

    def s_GeometryNodeSetMeshNormal(self, node, name, sock, dom):
        if node["props"].get("mode") != "FREE":
            raise _Unsupported("sharpness / tangent-space normal modes")
        self.emit(f"@N = {self.value(node, 'Custom Normal', 'vector')[0]};")

    def s_GeometryNodeSetMaterialIndex(self, node, name, sock, dom):
        self.guarded(self.selection(node, "FACE"),
                     f"@material_index = {self.value(node, 'Material Index', 'int')[0]};")

    def s_GeometryNodeSetPointRadius(self, node, name, sock, dom):
        self.guarded(self.selection(node, "POINT"), f"@pscale = {self.value(node, 'Radius')[0]};")

    s_GeometryNodeSetCurveRadius = s_GeometryNodeSetPointRadius

    def s_GeometryNodeSetCurveTilt(self, node, name, sock, dom):
        self.guarded(self.selection(node, "POINT"), f"f@tilt = {self.value(node, 'Tilt')[0]};")

    def s_GeometryNodeSetShadeSmooth(self, node, name, sock, dom):
        self.guarded(self.selection(node, "FACE"), f"shadesmooth({self.value(node, 'Shade Smooth', 'bool')[0]});")

    def s_GeometryNodeCaptureAttribute(self, node, name, sock, dom):
        items = node["items"]
        item_inputs = [s for s in node["inputs"] if s["type"] not in ("GEOMETRY",) and s["id"] not in
                       ("Selection",) and not s["id"].startswith("__extend__")]
        item_outputs = [s for s in node["outputs"] if s["type"] != "GEOMETRY" and s["id"] not in ("Selection",)
                        and not s["id"].startswith("__extend__")]
        for k, (s_in, s_out) in enumerate(zip(item_inputs, item_outputs)):
            if not s_in["links"]:
                continue
            e = self.value(node, s_in["id"])
            t = _SOCK_T.get(s_out["type"], "float")
            base = items[k][0] if k < len(items) and items[k][0] else "captured"
            local = self.fresh(base)
            self.emit(f"{_DECL.get(t, 'float')} {local} = {e[0]};")
            self.locals[(name, s_out["id"])] = (local, t)

    def s_GeometryNodeDeleteGeometry(self, node, name, sock, dom):
        sel = self.selection(node, dom)
        d = node["props"].get("domain", "POINT")
        if d == "POINT":
            self.guarded(sel, "removepoint(0, @ptnum);")
        elif d in ("FACE", "CURVE"):
            mode = node["props"].get("mode", "ALL")
            self.guarded(sel, f"removeprim(0, @primnum, {1 if mode == 'ALL' else 0});")
        else:
            raise _Unsupported(f"deleting {d.lower()}s")
        self.note("removepoint()/removeprim() apply when the script ends — if later nodes depended on the "
                  "deletion having happened, check the result")

    def s_GeometryNodeSubdivideMesh(self, node, name, sock, dom):
        self.emit(f"subdivide({self.value(node, 'Level', 'int')[0]});")

    def s_GeometryNodeSubdivisionSurface(self, node, name, sock, dom):
        self.emit(f"subdivsurf({self.value(node, 'Level', 'int')[0]});")

    def s_GeometryNodeTriangulate(self, node, name, sock, dom):
        self.guarded(self.selection(node, "FACE"), "triangulate();")

    def s_GeometryNodeDualMesh(self, node, name, sock, dom):
        self.emit("dualmesh();")

    def s_GeometryNodeConvexHull(self, node, name, sock, dom):
        self.emit("convexhull();")

    def s_GeometryNodeMergeByDistance(self, node, name, sock, dom):
        self.guarded(self.selection(node, "POINT"), f"fuse({self.value(node, 'Distance')[0]});")

    def s_GeometryNodeRealizeInstances(self, node, name, sock, dom):
        self.emit("realize();")

    def s_GeometryNodeFlipFaces(self, node, name, sock, dom):
        self.guarded(self.selection(node, "FACE"), "flipfaces();")

    def s_GeometryNodeExtrudeMesh(self, node, name, sock, dom):
        if node["props"].get("mode") != "FACES":
            raise _Unsupported("extruding vertices or edges")
        sel = self.selection(node, "FACE")
        off = self.inp(node, "Offset")
        if off["links"]:
            self.note("Extrude's custom offset vector became an offset along the normals")
        ind = self.value(node, "Individual", "bool")
        self.guarded(sel, f"extrude({self.value(node, 'Offset Scale')[0]}, {ind[0]});")

    def s_GeometryNodeMeshBevel(self, node, name, sock, dom):
        self.guarded(self.selection(node, "EDGE"),
                     f"bevel({self.value(node, 'Offset')[0]}, {self.value(node, 'Segments', 'int')[0]});")

    def s_GeometryNodeResampleCurve(self, node, name, sock, dom):
        self.emit(f"resample({self.value(node, 'Count', 'int')[0]});")

    def s_GeometryNodeMeshToCurve(self, node, name, sock, dom):
        self.emit("tocurves();")

    def s_GeometryNodeMeshToPoints(self, node, name, sock, dom):
        self.emit("topoints();")

    def s_GeometryNodeCurveToMesh(self, node, name, sock, dom):
        prof = self.link(node, "Profile Curve")
        res = "12"
        radius = 1.0
        if prof and self.nodes[prof[0]]["idname"] == "GeometryNodeCurvePrimitiveCircle":
            circ = self.nodes[prof[0]]
            res = self.value(circ, "Resolution", "int")[0]
            radius = self.inp(circ, "Radius")["value"] or 1.0
        elif prof:
            raise _Unsupported("a profile that isn't a circle")
        scale = self.value(node, "Scale")
        if radius != 1.0:
            scale = self.binop(scale, "*", (num(radius), "float", ATOM), MUL, "float")
        self.emit(f"sweep({scale[0]}, {res});")

    def s_GeometryNodeTransform(self, node, name, sock, dom):
        if self.inp(node, "Mode") and self.inp(node, "Mode")["value"] == "Matrix":
            raise _Unsupported("matrix mode")
        self.emit(f"transform({self.value(node, 'Translation', 'vector')[0]}, {self.value(node, 'Rotation')[0]}, "
                  f"{self.value(node, 'Scale', 'vector')[0]});")

    def s_GeometryNodeDistributePointsOnFaces(self, node, name, sock, dom):
        sel = self.selection(node, "FACE")
        seed = self.value(node, "Seed", "int")[0]
        if node["props"].get("distribute_method") == "POISSON":
            call = f"scatter({self.value(node, 'Density Max')[0]}, {seed}, {self.value(node, 'Distance Min')[0]});"
        else:
            call = f"scatter({self.value(node, 'Density')[0]}, {seed});"
        self.guarded(sel, call)
        self.note("scatter() also stores @N and p@orient on the points")

    def s_GeometryNodeInstanceOnPoints(self, node, name, sock, dom):
        src = self.link(node, "Instance")
        if src is None:
            raise _Unsupported("nothing to instance")
        s = self.nodes[src[0]]
        if s["idname"] == "GeometryNodeObjectInfo":
            ob = self.link(s, "Object")
            it = self.iface.get(ob[1]) if ob else None
            if it is None:
                raise _Unsupported("instanced object chosen inside the tree")
            ref = f'chobj("{it["name"]}")'
        elif s["idname"] == "GeometryNodeCollectionInfo":
            co = self.link(s, "Collection")
            it = self.iface.get(co[1]) if co else None
            if it is None:
                raise _Unsupported("instanced collection chosen inside the tree")
            ref = f'chcoll("{it["name"]}")'
        else:
            raise _Unsupported("instancing generated geometry")
        rot, scale = self.inp(node, "Rotation"), self.inp(node, "Scale")
        if rot["links"] and not self._attr_or_default(rot["links"][0], "orient"):
            self.emit(f"p@orient = {self.value(node, 'Rotation')[0]};")
        if scale["links"]:
            lk = scale["links"][0]
            sn = self.nodes[lk[0]]
            if sn["idname"] == "ShaderNodeVectorMath" and sn["props"].get("operation") == "SCALE":
                r = self.link(sn, "Scale")
                if r and self.nodes[r[0]]["idname"] == "GeometryNodeInputRadius":
                    lk = self.link(sn, "Vector")
            if not (lk and self._attr_or_default(lk, "scale")):
                self.emit(f"v@scale = {self.value(node, 'Scale', 'vector')[0]};")
        self.guarded(self.selection(node, "POINT"), f"instance({ref});")

    def _attr_or_default(self, link, attr):
        """True for Switch(attribute exists, default, attribute) — what instance() wires up."""
        sw = self.nodes[link[0]]
        if sw["idname"] != "GeometryNodeSwitch":
            return False
        c, t = self.link(sw, "Switch"), self.link(sw, "True")
        if not (c and t and c[0] == t[0] and c[1] == "Exists" and not self.link(sw, "False")):
            return False
        na = self.nodes[c[0]]
        return na["idname"] == "GeometryNodeInputNamedAttribute" and self.inp(na, "Name")["value"] == attr

    def s_GeometryNodeWarning(self, node, name, sock, dom):
        pass

    def s_GeometryNodeGetGeometryBundle(self, node, name, sock, dom):
        pass

    def s_GeometryNodeSwitch(self, node, name, sock, dom):
        c, f, t = self.link(node, "Switch"), self.link(node, "False"), self.link(node, "True")
        if not (c and f == t and self.nodes[c[0]]["idname"] == "GeometryNodeWarning"):
            raise _Unsupported("switching between geometries")
        w = self.nodes[c[0]]
        fn = {"INFO": "printf", "WARNING": "warning", "ERROR": "error"}.get(w["props"].get("warning_type"), "printf")
        args = self.message_args(w)
        show = self.inp(w, "Show")
        cond = None
        if show["links"]:
            src = self.nodes[show["links"][0][0]]
            if src["idname"] != "FunctionNodeInputBool" or not src["props"].get("boolean"):
                cond = self.per_element_condition(show["links"][0])
        elif not show["value"]:
            return
        self.guarded(cond, f"{fn}({', '.join(args)});")

    def per_element_condition(self, link):
        """maxof(cond) > 0.5 (what an if around printf() becomes) → cond again."""
        cmp_ = self.nodes[link[0]]
        if cmp_["idname"] == "FunctionNodeCompare" and cmp_["props"].get("operation") == "GREATER_THAN":
            a = self.link(cmp_, "A")
            if a and a[1] == "Max" and self.nodes[a[0]]["idname"] == "GeometryNodeAttributeStatistic":
                st = self.nodes[a[0]]
                saved, self.domain = self.domain, st["props"].get("domain", "POINT")
                try:
                    return self.value(st, "Attribute")[0]
                finally:
                    self.domain = saved
        return self.expr(*link)[0]

    def message_args(self, w):
        msg = self.inp(w, "Message")
        if not msg["links"]:
            return [_quote(str(msg["value"] or "").replace("%", "%%"))]
        src = self.nodes[msg["links"][0][0]]
        if src["idname"] != "FunctionNodeFormatString":
            return ['"%s"', self.expr(*msg["links"][0])[0]]
        fmt_in = self.inp(src, "Format")
        if fmt_in["links"]:
            raise _Unsupported("a format text that comes from a link")
        kinds = dict(src["items"])
        keys = [s["id"] for s in src["inputs"] if s["id"].startswith("Item_") and s["enabled"]]
        names = [s["name"] for s in src["inputs"] if s["id"].startswith("Item_") and s["enabled"]]
        out, args, pos = [], [], 0
        text = str(fmt_in["value"] or "")
        for m in _FMT_FIELD.finditer(text):
            out.append(text[pos:m.start()].replace("{{", "{").replace("}}", "}").replace("%", "%%"))
            pos = m.end()
            if m.group(0) in ("{{", "}}"):
                out.append(m.group(0)[0])
                continue
            key, spec = m.group("key"), m.group("spec") or ""
            if key not in names:
                raise _Unsupported(f"format field {key}")
            kind = kinds.get(key, "FLOAT")
            out.append(_printf_spec(spec, kind))
            args.append(self.value(src, keys[names.index(key)], "float")[0])
        out.append(text[pos:].replace("{{", "{").replace("}}", "}").replace("%", "%%"))
        return [_quote("".join(out))] + args

    def s_GeometryNodeSetGeometryBundle(self, node, name, sock, dom):
        lk = self.link(node, "Bundle")
        stores = []
        while lk is not None and self.nodes[lk[0]]["idname"] == "NodeStoreBundleItem":
            sn = self.nodes[lk[0]]
            stores.append(sn)
            lk = self.link(sn, "Bundle")
        if not stores:
            raise _Unsupported("bundles")
        self.emit("runover(detail) {")
        self.indent += 1
        self.domain = "POINT"
        for sn in reversed(stores):
            path = self.inp(sn, "Path")["value"]
            st = sn["props"].get("socket_type", "FLOAT")
            pre = {"FLOAT": "f", "INT": "i", "BOOLEAN": "b", "VECTOR": "v", "ROTATION": "p", "MATRIX": "4",
                   "STRING": "s"}.get(st, "f")
            self.emit(f"{pre}@{path} = {self.value(sn, 'Item')[0]};")
        self.indent -= 1
        self.emit("}")

    def x_NodeGetBundleItem(self, node, sock):
        b = self.link(node, "Bundle")
        if not (b and self.nodes[b[0]]["idname"] == "GeometryNodeGetGeometryBundle"):
            raise _Unsupported("bundles")
        st = node["props"].get("socket_type", "FLOAT")
        pre = {"FLOAT": "f", "INT": "i", "BOOLEAN": "b", "VECTOR": "v", "ROTATION": "p", "MATRIX": "4",
               "STRING": "s"}.get(st, "f")
        path = self.inp(node, "Path")["value"]
        return f'detail(0, "{pre}@{path}")', _SOCK_T.get({"FLOAT": "VALUE"}.get(st, st), "float"), ATOM

    # zones
    def _zone_items(self, zin, zout, name):
        """Bind zone item outputs (other than geometry) to locals; return [(local, out_key, type)]."""
        carried = []
        items = self.nodes[zout]["items"]
        for k, (iname, stype) in enumerate(items):
            if stype == "GEOMETRY" or k == 0:
                continue
            key = f"Item_{k}"
            t = {"FLOAT": "float", "INT": "int", "BOOLEAN": "bool", "VECTOR": "vector", "ROTATION": "rotation",
                 "MATRIX": "matrix", "RGBA": "vector", "STRING": "string"}.get(stype, "float")
            local = self.fresh(iname or f"item{k}")
            init = self.value(self.nodes[zin], key)
            self.emit(f"{_DECL.get(t, 'float')} {local} = {init[0]};")
            self.locals[(zin, key)] = (local, t)
            carried.append((local, key, t))
        return carried

    def _zone_body(self, zin, zout, carried):
        gin = self.inp(self.nodes[zout], "Item_0") or self._geo_input(self.nodes[zout])
        body = self.chain(gin["links"][0], stop=zin) if gin and gin["links"] else []
        self.block(body)
        for local, key, t in carried:
            e = self.value(self.nodes[zout], key)
            if e[0] != local:
                self.emit(f"{local} = {e[0]};")
        for local, key, t in carried:
            self.locals[(zout, key)] = (local, t)

    def s_GeometryNodeSimulationOutput(self, node, name, sock, dom):
        zin = next(n for n, d in self.nodes.items() if d.get("pair") == name)
        self.reserved[(zin, "Delta Time")] = "deltatime"
        carried = self._zone_items(zin, name, name)
        self.emit("simulate {")
        self.indent += 1
        self._zone_body(zin, name, carried)
        self.indent -= 1
        self.emit("}")

    def s_GeometryNodeRepeatOutput(self, node, name, sock, dom):
        zin = next(n for n, d in self.nodes.items() if d.get("pair") == name)
        self.reserved[(zin, "Iteration")] = "iteration"
        count = self.value(self.nodes[zin], "Iterations", "int")
        carried = self._zone_items(zin, name, name)
        self.emit(f"repeat({count[0]}) {{")
        self.indent += 1
        self._zone_body(zin, name, carried)
        self.indent -= 1
        self.emit("}")

    def s_GeometryNodeForeachGeometryElementOutput(self, node, name, sock, dom):
        zin = next(n for n, d in self.nodes.items() if d.get("pair") == name)
        self.reserved[(zin, "Index")] = "elemindex"
        d = _DOMAIN_WORD.get(node["props"].get("domain", "POINT"), "point")
        gin = self.inp(node, "Generation_0")
        body = self.chain(gin["links"][0], stop=zin) if gin and gin["links"] else []
        self.emit(f"foreach({d}) {{")
        self.indent += 1
        self.block(body)
        self.indent -= 1
        self.emit("}")


_FMT_FIELD = __import__("re").compile(r"\{\{|\}\}|\{(?P<key>\w+)(?::(?P<spec>[^}]*))?\}")
_SPEC_RE = __import__("re").compile(r"(?P<align><)?(?P<sign>[+ ])?(?P<zero>0)?(?P<width>\d+)?(?:\.(?P<prec>\d+))?"
                                    r"(?P<conv>[a-zA-Z])?$")


def _quote(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _printf_spec(spec, kind):
    """A Format String field spec ('.3f', '03', '') → a printf placeholder ('%.3f', '%03d', '%d')."""
    m = _SPEC_RE.match(spec or "")
    if m is None:
        return "%g" if kind == "FLOAT" else "%s" if kind == "STRING" else "%d"
    conv = m.group("conv") or {"INT": "d", "STRING": "s"}.get(kind, "g")
    if conv == "f" and m.group("prec") == "6" and not (m.group("width") or m.group("zero") or m.group("sign")):
        return "%f"
    return ("%" + ("-" if m.group("align") else "") + (m.group("sign") or "") + (m.group("zero") or "")
            + (m.group("width") or "") + ("." + m.group("prec") if m.group("prec") is not None else "") + conv)


def _isnum(text):
    try:
        float(text)
        return True
    except (TypeError, ValueError):
        return False


def decompile(data):
    """(script, notes) for an extract()-ed node tree."""
    d = Decompiler(data)
    script, notes = d.run()
    header = f"// converted from the node group '{data.get('name', '')}'\n"
    return header + script, notes
