# SPDX-License-Identifier: GPL-3.0-or-later
"""Math, vector, mapping and conversion functions (+ constant folding)."""

import ast
import math

from .core import FLOAT, INT, BOOL, VECTOR, ROTATION, Val, FUNCS, reg, FormulaError, type_word

VM_SCALAR_OUT = {"DOT_PRODUCT", "DISTANCE", "LENGTH"}


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
    "MULTIPLY_ADD": lambda a, b, c: a * b + c,
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
    "LESS_THAN": lambda a, b: float(a < b), "GREATER_THAN": lambda a, b: float(a > b),
}


def fold(fn, *args):
    try:
        r = fn(*args)
    except (ValueError, OverflowError, ZeroDivisionError, TypeError):
        return None
    if isinstance(r, tuple):
        return None if any(math.isnan(x) or math.isinf(x) for x in r) else tuple(float(x) for x in r)
    r = float(r)
    return None if (math.isnan(r) or math.isinf(r)) else r


def vfold(op, vs, s):
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
#  Registry
# ═════════════════════════════════════════════════════════════════════════════

# Math — work on floats; the "poly" ones also work component-wise on vectors
for _n, _m, _v in [("sin", "SINE", "SINE"), ("cos", "COSINE", "COSINE"), ("tan", "TANGENT", "TANGENT"),
                   ("abs", "ABSOLUTE", "ABSOLUTE"), ("sign", "SIGN", "SIGN"),
                   ("floor", "FLOOR", "FLOOR"), ("ceil", "CEIL", "CEIL"),
                   ("round", "ROUND", "ROUND"), ("rint", "ROUND", "ROUND"),
                   ("fract", "FRACT", "FRACTION"), ("frac", "FRACT", "FRACTION")]:
    reg(_n, "NAME(x)", "float or per-component on vectors", "Math", "f_poly", mop=_m, vop=_v, n=1)
for _n, _m in [("asin", "ARCSINE"), ("acos", "ARCCOSINE"), ("sinh", "SINH"), ("cosh", "COSH"),
               ("tanh", "TANH"), ("sqrt", "SQRT"), ("invsqrt", "INVERSE_SQRT"), ("exp", "EXPONENT"),
               ("trunc", "TRUNC"), ("radians", "RADIANS"), ("degrees", "DEGREES")]:
    reg(_n, "NAME(x)", "float", "Math", "f_poly", mop=_m, vop=None, n=1)
for _n, _m, _v in [("pow", "POWER", "POWER"), ("mod", "MODULO", "MODULO"), ("fmod", "MODULO", "MODULO"),
                   ("snap", "SNAP", "SNAP")]:
    reg(_n, "NAME(a, b)", "float or per-component on vectors", "Math", "f_poly", mop=_m, vop=_v, n=2)
for _n, _m in [("atan2", "ARCTAN2"), ("flooredmod", "FLOORED_MODULO"), ("pingpong", "PINGPONG"),
               ("less", "LESS_THAN"), ("greater", "GREATER_THAN")]:
    reg(_n, "NAME(a, b)", "float", "Math", "f_poly", mop=_m, vop=None, n=2)
reg("wrap", "wrap(x, max, min)", "wraps x into [min, max); floats or vectors", "Math", "f_poly",
    mop="WRAP", vop="WRAP", n=3)
reg("multiplyadd", "multiplyadd(a, b, c)", "a * b + c", "Math", "f_poly", mop="MULTIPLY_ADD", vop="MULTIPLY_ADD", n=3)
reg("smoothmin", "smoothmin(a, b, distance)", "soft minimum", "Math", "f_poly", mop="SMOOTH_MIN", vop=None, n=3)
reg("smoothmax", "smoothmax(a, b, distance)", "soft maximum", "Math", "f_poly", mop="SMOOTH_MAX", vop=None, n=3)
reg("min max", "NAME(a, b, ...)", "2 or more values; per-component on vectors; on an array: its smallest/largest item",
    "Math", "f_minmax")
reg("avg", "avg(a, b, ...)", "average of the values (or of an array)", "Math", "f_avg")
reg("atan", "atan(x) or atan(y, x)", "arc tangent", "Math", "f_atan")
reg("log", "log(x) or log(x, base)", "natural log by default", "Math", "f_log")
reg("log10", "log10(x)", "base-10 logarithm", "Math", "f_log10")

# Vector
reg("length", "length(v)", "vector length", "Vector", "f_vec", op="LENGTH", n=1)
reg("normalize", "normalize(v)", "unit vector (zero stays zero)", "Vector", "f_vec", op="NORMALIZE", n=1)
reg("dot", "dot(a, b)", "dot product", "Vector", "f_vec", op="DOT_PRODUCT", n=2)
reg("cross", "cross(a, b)", "cross product", "Vector", "f_vec", op="CROSS_PRODUCT", n=2)
reg("distance", "distance(a, b)", "distance between points", "Vector", "f_vec", op="DISTANCE", n=2)
reg("project", "project(a, b)", "projects a onto b", "Vector", "f_vec", op="PROJECT", n=2)
reg("reflect", "reflect(v, normal)", "reflection", "Vector", "f_vec", op="REFLECT", n=2)
reg("faceforward", "faceforward(v, incident, reference)", "orients v", "Vector", "f_vec", op="FACEFORWARD", n=3)
reg("refract", "refract(v, normal, ior)", "refraction", "Vector", "f_refract")
reg("scale", "scale(v, s)", "multiplies a vector by a float (or per component by a vector)", "Vector", "f_scale")
reg("length2", "length2(v)", "squared length", "Vector", "f_length2")
reg("distance2", "distance2(a, b)", "squared distance", "Vector", "f_distance2")
for _n, _v in [("vabs", "ABSOLUTE"), ("vfloor", "FLOOR"), ("vceil", "CEIL"), ("vfract", "FRACTION"),
               ("vsin", "SINE"), ("vcos", "COSINE"), ("vtan", "TANGENT")]:
    reg(_n, "NAME(v)", "per-component on a vector", "Vector", "f_vec", op=_v, n=1)
for _n, _v in [("vmin", "MINIMUM"), ("vmax", "MAXIMUM"), ("vmod", "MODULO"), ("vsnap", "SNAP")]:
    reg(_n, "NAME(a, b)", "per-component on vectors", "Vector", "f_vec", op=_v, n=2)
reg("vwrap", "vwrap(v, max, min)", "per-component wrap", "Vector", "f_vec", op="WRAP", n=3)
reg("rotate", "rotate(v, axis, angle)", "rotates v around axis (radians)", "Vector", "f_rotate")
reg("slerpv", "slerpv(a, b, t)", "spherical blend between two directions", "Vector", "f_slerpv")

# Mapping & blending
reg("fit", "fit(x, old_min, old_max, new_min, new_max)", "remap, clamped; floats or vectors",
    "Mapping & blending", "f_fit", clamp=True)
reg("efit", "efit(x, old_min, old_max, new_min, new_max)", "remap without clamping",
    "Mapping & blending", "f_fit", clamp=False)
reg("fit01", "fit01(x, new_min, new_max)", "remap from 0..1", "Mapping & blending", "f_fit", clamp=True, src=(0.0, 1.0))
reg("fit10", "fit10(x, new_min, new_max)", "remap from 1..0", "Mapping & blending", "f_fit", clamp=True, src=(1.0, 0.0))
reg("fit11", "fit11(x, new_min, new_max)", "remap from -1..1", "Mapping & blending", "f_fit", clamp=True, src=(-1.0, 1.0))
reg("smoothstep smooth", "NAME(edge0, edge1, x)", "0..1 smooth ramp", "Mapping & blending", "f_smoothstep")
reg("smootherstep", "smootherstep(edge0, edge1, x)", "0..1 extra-smooth ramp", "Mapping & blending",
    "f_smoothstep", interp="SMOOTHERSTEP")
reg("clamp", "clamp(x, min, max)", "floats or vectors", "Mapping & blending", "f_clamp")
reg("lerp mix", "NAME(a, b, t)", "linear blend; floats, vectors or rotations", "Mapping & blending", "f_lerp")
reg("invlerp", "invlerp(a, b, x)", "where x sits between a and b (0..1, unclamped)", "Mapping & blending", "f_invlerp")
reg("bias", "bias(x, b)", "Perlin bias: pushes 0..1 values toward 0 (b<0.5) or 1 (b>0.5)", "Mapping & blending", "f_bias")
reg("gain", "gain(x, g)", "Perlin gain: S-curve contrast around 0.5 (g>0.5 = more contrast)", "Mapping & blending", "f_gain")
reg("steps quantize", "NAME(x, count)", "snaps 0..1 values to count steps", "Mapping & blending", "f_steps")

# Conversion
reg("set vec vector", "NAME(x, y, z) or NAME(x)", "builds a vector (4 values build a rotation)", "Conversion", "f_vector")
reg("float", "float(x)", "to float", "Conversion", "f_cast", t=FLOAT)
reg("int", "int(x)", "to int (truncates)", "Conversion", "f_cast", t=INT)
reg("bool", "bool(x)", "to bool (non-zero is true)", "Conversion", "f_cast", t=BOOL)
for _n, _k, _d in [("sample_direction_uniform", "direction", "uniformly distributed unit vector from u = (0..1, 0..1)"),
                   ("sample_sphere_uniform", "sphere", "uniform point inside the unit sphere from u = (0..1, 0..1, 0..1)"),
                   ("sample_hemisphere", "hemisphere", "uniform direction on the +Z hemisphere from u = (0..1, 0..1)"),
                   ("sample_disk_uniform", "disk", "uniform point on the unit disk (XY) from u = (0..1, 0..1)"),
                   ("sample_circle_uniform", "circle", "point on the unit circle (XY) from u (0..1)")]:
    reg(_n, f"{_n}(u)", _d, "Noise & random", "f_sample", kind=_k)
reg("getcomp", "getcomp(v, i)", "component i (0, 1, 2) of a vector; i may vary per element", "Conversion", "f_getcomp")


class MathFuncs:
    def f_poly(self, node, name):
        d = FUNCS[name].data
        vals = self.args(node, name, {d["n"]})
        if any(v.t == VECTOR for v in vals):
            if d["vop"] is None:
                raise FormulaError(f"{name}() works on floats only — apply it to a component (.x .y .z)")
            return self.vmath(d["vop"], *vals)
        return self.math(d["mop"], *vals, what=f"{name}()'s argument")

    def f_minmax(self, node, name):
        if len(node.args) == 1:
            v = self.expr(node.args[0])
            if v.t.startswith("LIST:"):
                return self.list_reduce(v, "Min" if name == "min" else "Max")
            raise FormulaError(f"{name}() needs at least 2 arguments: {name}(a, b) — or one array")
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

    def f_avg(self, node, name):
        if not node.args:
            raise FormulaError("avg() needs values: avg(a, b, ...) or avg(array)")
        if len(node.args) == 1:
            v = self.expr(node.args[0])
            if v.t.startswith("LIST:"):
                return self.list_reduce(v, "Mean")
            return v
        vals = [self.expr(a) for a in node.args]
        acc = vals[0]
        for v in vals[1:]:
            acc = self.binop(ast.Add, acc, v)
        return self.binop(ast.Div, acc, Val(FLOAT, c=float(len(vals))))

    def f_atan(self, node, name):
        vals = self.args(node, name, {1, 2})
        if len(vals) == 1:
            return self.math("ARCTANGENT", vals[0], what="atan()'s argument")
        return self.math("ARCTAN2", vals[0], vals[1], what="atan()'s argument")

    def f_log(self, node, name):
        vals = self.args(node, name, {1, 2})
        base = vals[1] if len(vals) == 2 else Val(FLOAT, c=math.e)
        return self.math("LOGARITHM", vals[0], base, what="log()'s argument")

    def f_log10(self, node, name):
        (v,) = self.args(node, name, {1})
        return self.math("LOGARITHM", v, Val(FLOAT, c=10.0), what="log10()'s argument")

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
        if s.t == VECTOR:
            return self.vmath("MULTIPLY", v, s)
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

    def f_slerpv(self, node, name):
        a, b, t = self.args(node, name, {3})
        a, b = self.coerce(a, VECTOR), self.coerce(b, VECTOR)
        t = self.coerce(t, FLOAT, "slerpv()'s blend factor")
        # rotate a toward b around their common axis by t * angle, keep the lengths blended
        na, nb = self.vmath("NORMALIZE", a), self.vmath("NORMALIZE", b)
        axis = self.vmath("CROSS_PRODUCT", na, nb)
        ang = self.math("ARCCOSINE", self.math("MINIMUM", self.math("MAXIMUM", self.vmath("DOT_PRODUCT", na, nb),
                                                                    Val(FLOAT, c=-1.0)), Val(FLOAT, c=1.0)))
        n = self.g.add("ShaderNodeVectorRotate", {"rotation_type": "AXIS_ANGLE"},
                       {"Vector": self.inp(na, VECTOR), "Axis": self.inp(axis, VECTOR),
                        "Angle": self.inp(self.math("MULTIPLY", ang, t), FLOAT)})
        dirv = Val(VECTOR, o=n.out("Vector"), field=a.field or b.field or t.field)
        la = self.vmath("LENGTH", a)
        lb = self.vmath("LENGTH", b)
        return self.vmath("SCALE", dirv, scale=self.f_lerp_vals(la, lb, t))

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
        interp = FUNCS[name].data.get("interp", "SMOOTHSTEP")
        n = self.g.add("ShaderNodeMapRange",
                       {"data_type": "FLOAT", "interpolation_type": interp, "clamp": True},
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
        if ROTATION in (a.t, b.t):
            return self.rot_slerp(a, b, t)
        return self.f_lerp_vals(a, b, t, name)

    def f_lerp_vals(self, a, b, t, name="lerp"):
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

    def f_invlerp(self, node, name):
        a, b, x = [self.coerce(v, FLOAT, "invlerp()'s argument") for v in self.args(node, name, {3})]
        return self.math("DIVIDE", self.math("SUBTRACT", x, a), self.math("SUBTRACT", b, a))

    def f_bias(self, node, name):
        # Perlin: x ^ (log(b) / log(0.5))
        x, b = [self.coerce(v, FLOAT, "bias()'s argument") for v in self.args(node, name, {2})]
        expo = self.math("DIVIDE", self.math("LOGARITHM", b, Val(FLOAT, c=math.e)), Val(FLOAT, c=math.log(0.5)))
        return self.math("POWER", self.math("MAXIMUM", x, Val(FLOAT, c=0.0)), expo)

    def f_gain(self, node, name):
        # Perlin: x < 0.5 ? bias(2x, 1-g)/2 : 1 - bias(2-2x, 1-g)/2
        x, g = [self.coerce(v, FLOAT, "gain()'s argument") for v in self.args(node, name, {2})]
        one_minus_g = self.math("SUBTRACT", Val(FLOAT, c=1.0), g)
        expo = self.math("DIVIDE", self.math("LOGARITHM", one_minus_g, Val(FLOAT, c=math.e)),
                         Val(FLOAT, c=math.log(0.5)))
        lo = self.math("MULTIPLY", self.math("POWER", self.math("MULTIPLY", x, Val(FLOAT, c=2.0)), expo),
                       Val(FLOAT, c=0.5))
        hi_in = self.math("SUBTRACT", Val(FLOAT, c=2.0), self.math("MULTIPLY", x, Val(FLOAT, c=2.0)))
        hi = self.math("SUBTRACT", Val(FLOAT, c=1.0),
                       self.math("MULTIPLY", self.math("POWER", hi_in, expo), Val(FLOAT, c=0.5)))
        return self.switch(self.compare("LESS_THAN", x, Val(FLOAT, c=0.5)), hi, lo, FLOAT)

    def f_steps(self, node, name):
        x, count = [self.coerce(v, FLOAT, f"{name}()'s argument") for v in self.args(node, name, {2})]
        return self.math("DIVIDE", self.math("FLOOR", self.math("MULTIPLY", x, count)), count)

    def f_vector(self, node, name):
        if len(node.args) == 1 and name == "vector":
            # VEX's signature cast: vector(rand(x)) picks the vector form of rand(), noise(), ...
            return self.coerce(self.expr_hint(node.args[0], VECTOR), VECTOR, f"{name}()'s argument")
        vals = self.args(node, name, {1, 2, 3, 4})
        if len(vals) == 1:
            return self.coerce(vals[0], VECTOR, f"{name}()'s argument")
        if len(vals) == 2:           # Houdini's vector2: set(x, y)
            return self.combine(vals + [Val(FLOAT, c=0.0)])
        if len(vals) == 4:
            return self.quat_literal(vals)
        return self.combine(vals)

    def f_cast(self, node, name):
        (v,) = self.args(node, name, {1})
        t = FUNCS[name].data["t"]
        if v.t == "STRING":
            if t in (FLOAT, INT):
                return self.string_to_number(v, t)
        if t == BOOL:
            return self.as_bool(v, "bool()'s argument")
        if t == INT and v.t == FLOAT:
            return self.coerce(self.math("TRUNC", v), INT)
        return self.coerce(v, t, f"{name}()'s argument")

    def f_sample(self, node, name):
        """Houdini's sample_*() family: maps uniform random numbers to directions and points."""
        (u,) = self.args(node, name, {1})
        kind = FUNCS[name].data["kind"]
        if u.t in (FLOAT, INT, BOOL):
            u = self.combine([self.coerce(u, FLOAT), Val(FLOAT, c=0.0), Val(FLOAT, c=0.0)])
        u = self.coerce(u, VECTOR, f"{name}()'s argument")
        ux, uy, uz = (self.sep(u, i) for i in range(3))
        one, zero, tau = Val(FLOAT, c=1.0), Val(FLOAT, c=0.0), Val(FLOAT, c=2 * math.pi)
        if kind in ("circle", "disk"):
            phi = self.math("MULTIPLY", ux, tau)
            rad = one if kind == "circle" else self.math("SQRT", uy)
            return self.combine([self.math("MULTIPLY", rad, self.math("COSINE", phi)),
                                 self.math("MULTIPLY", rad, self.math("SINE", phi)), zero])
        z = ux if kind == "hemisphere" else self.math("SUBTRACT", one, self.math("MULTIPLY", ux, Val(FLOAT, c=2.0)))
        r = self.math("SQRT", self.math("MAXIMUM", self.math("SUBTRACT", one, self.math("MULTIPLY", z, z)), zero))
        phi = self.math("MULTIPLY", uy, tau)
        d = self.combine([self.math("MULTIPLY", r, self.math("COSINE", phi)),
                          self.math("MULTIPLY", r, self.math("SINE", phi)), z])
        if kind == "sphere":
            return self.vmath("SCALE", d, scale=self.math("POWER", uz, Val(FLOAT, c=1.0 / 3.0)))
        return d

    def f_getcomp(self, node, name):
        if len(node.args) != 2:
            raise FormulaError("getcomp() takes a vector and an index: getcomp(v, 2)")
        v = self.expr(node.args[0])
        if v.t == ROTATION:
            idx = node.args[1]
            if isinstance(idx, ast.Constant) and idx.value in (0, 1, 2, 3):
                return self.rot_component(v, "xyzw"[int(idx.value)])
            raise FormulaError("getcomp() on a rotation needs a fixed index 0..3")
        if v.t != VECTOR:
            raise FormulaError(f"getcomp() needs a vector, not a {type_word(v.t)}")
        return self.vector_component(v, self.coerce(self.expr(node.args[1]), INT, "getcomp()'s index"))
