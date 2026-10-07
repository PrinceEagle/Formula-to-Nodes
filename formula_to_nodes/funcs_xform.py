# SPDX-License-Identifier: GPL-3.0-or-later
"""Rotations (VEX vector4 quaternions → Blender rotation sockets), 4x4
matrices and colour functions."""

import ast
import math

from .core import (FLOAT, INT, BOOL, VECTOR, ROTATION, MATRIX, Val, FUNCS, reg, FormulaError, type_word,
                   quat_to_euler)

# Rotation & matrix
reg("quaternion", "quaternion(angle, axis) or quaternion(angleaxis) or quaternion(matrix)",
    "rotation from an angle (radians) around an axis", "Rotation & matrix", "f_quaternion")
reg("eulertoquaternion euler", "NAME(angles [, order])", "rotation from XYZ Euler angles (radians)",
    "Rotation & matrix", "f_eulertoquaternion")
reg("quaterniontoeuler", "quaterniontoeuler(q [, order])", "XYZ Euler angles (radians) of a rotation",
    "Rotation & matrix", "f_quaterniontoeuler")
reg("qrotate", "qrotate(q, v)", "rotates a vector by a rotation (also q * v)", "Rotation & matrix", "f_qrotate")
reg("qmultiply", "qmultiply(q1, q2)", "combined rotation: q2 then q1 (also q1 * q2)", "Rotation & matrix",
    "f_qmultiply")
reg("qinvert qconjugate", "NAME(q)", "inverse rotation", "Rotation & matrix", "f_qinvert")
reg("slerp", "slerp(q1, q2, t)", "spherical blend between rotations", "Rotation & matrix", "f_slerp")
reg("dihedral", "dihedral(a, b)", "rotation that turns direction a into direction b", "Rotation & matrix",
    "f_dihedral")
reg("lookat", "lookat(from, to [, up])", "rotation whose -Z axis looks from → to (Y toward up)",
    "Rotation & matrix", "f_lookat")
reg("alignaxis", 'alignaxis(dir [, up, "z"])', "rotation whose given axis points along dir (instancing)",
    "Rotation & matrix", "f_alignaxis")
reg("qangle", "qangle(q)", "rotation angle in radians", "Rotation & matrix", "f_qaxisangle", out="Angle")
reg("qaxis", "qaxis(q)", "rotation axis", "Rotation & matrix", "f_qaxisangle", out="Axis")
reg("ident", "ident()", "identity matrix", "Rotation & matrix", "f_ident")
reg("maketransform", "maketransform(translate, rotate, scale)",
    "4x4 matrix; rotate is a rotation or XYZ Euler radians, scale a vector or float", "Rotation & matrix",
    "f_maketransform")
reg("cracktransform", "cracktransform(m, c)", "part of a matrix: c = 0 translate, 1 rotate (degrees), 2 scale",
    "Rotation & matrix", "f_cracktransform")
reg("gettranslation", "gettranslation(m)", "translation of a matrix", "Rotation & matrix", "f_matpart",
    out="Translation")
reg("getrotation", "getrotation(m)", "rotation of a matrix", "Rotation & matrix", "f_matpart", out="Rotation")
reg("getscale", "getscale(m)", "scale of a matrix", "Rotation & matrix", "f_matpart", out="Scale")
reg("invert", "invert(m or q)", "inverse matrix or rotation", "Rotation & matrix", "f_invert")
reg("transpose", "transpose(m)", "transposed matrix", "Rotation & matrix", "f_matunary",
    node="FunctionNodeTransposeMatrix", out="Matrix", t=MATRIX)
reg("determinant", "determinant(m)", "matrix determinant", "Rotation & matrix", "f_matunary",
    node="FunctionNodeMatrixDeterminant", out="Determinant", t=FLOAT)
reg("transformpoint", "transformpoint(v, m)", "applies a matrix to a position (also v * m)", "Rotation & matrix",
    "f_transform", node="FunctionNodeTransformPoint", sock="Vector")
reg("transformdir", "transformdir(v, m)", "applies a matrix to a direction (no translation)", "Rotation & matrix",
    "f_transform", node="FunctionNodeTransformDirection", sock="Direction")

# Colour
reg("hsvtorgb", "hsvtorgb(hsv)", "HSV (0..1 each) to RGB", "Color", "f_colorspace", mode="HSV", to_rgb=True)
reg("rgbtohsv", "rgbtohsv(rgb)", "RGB to HSV (0..1 each)", "Color", "f_colorspace", mode="HSV", to_rgb=False)
reg("hsltorgb", "hsltorgb(hsl)", "HSL to RGB", "Color", "f_colorspace", mode="HSL", to_rgb=True)
reg("rgbtohsl", "rgbtohsl(rgb)", "RGB to HSL", "Color", "f_colorspace", mode="HSL", to_rgb=False)
reg("luminance", "luminance(rgb)", "perceived brightness (Rec. 709)", "Color", "f_luminance")
reg("blackbody", "blackbody(kelvin [, luminance])", "colour of a hot body (1000 K red .. 12000 K blue)", "Color",
    "f_blackbody")
reg("colormix", 'colormix(a, b, t [, "multiply"|"screen"|"overlay"|"add"|...])',
    "blends colours with a Photoshop-style mode", "Color", "f_colormix")

_BLEND = {"mix": "MIX", "darken": "DARKEN", "multiply": "MULTIPLY", "burn": "BURN", "lighten": "LIGHTEN",
          "screen": "SCREEN", "dodge": "DODGE", "add": "ADD", "overlay": "OVERLAY", "softlight": "SOFT_LIGHT",
          "linearlight": "LINEAR_LIGHT", "difference": "DIFFERENCE", "exclusion": "EXCLUSION",
          "subtract": "SUBTRACT", "divide": "DIVIDE", "hue": "HUE", "saturation": "SATURATION",
          "color": "COLOR", "value": "VALUE"}


class XformFuncs:
    # ── rotation helpers used by the core ──────────────────────────────────
    def quat_literal(self, vals):
        """{x, y, z, w} (VEX vector4 quaternion order) → rotation."""
        parts = [self.coerce(v, FLOAT, "a quaternion component") for v in vals]
        if all(p.is_const for p in parts):
            return Val(ROTATION, c=quat_to_euler(*[float(p.c) for p in parts]))
        x, y, z, w = parts
        n = self.g.add("FunctionNodeQuaternionToRotation", {},
                       {"W": self.inp(w, FLOAT), "X": self.inp(x, FLOAT), "Y": self.inp(y, FLOAT),
                        "Z": self.inp(z, FLOAT)})
        return Val(ROTATION, o=n.out("Rotation"), field=any(p.field for p in parts))

    def rot(self, v, what="the rotation"):
        return self.coerce(v, ROTATION, what)

    def rot_component(self, q, comp):
        q = self.rot(q)
        n = self.g.add("FunctionNodeRotationToQuaternion", {}, {"Rotation": self.inp(q, ROTATION)})
        return Val(FLOAT, o=n.out(comp.upper()), field=q.field)

    def rot_invert(self, q):
        q = self.rot(q)
        n = self.g.add("FunctionNodeInvertRotation", {}, {"Rotation": self.inp(q, ROTATION)})
        return Val(ROTATION, o=n.out("Rotation"), field=q.field)

    def rot_multiply(self, q1, q2):
        """q1 * q2: rotate by q2 first, then by q1 (VEX qmultiply order)."""
        q1, q2 = self.rot(q1), self.rot(q2)
        n = self.g.add("FunctionNodeRotateRotation", {"rotation_space": "GLOBAL"},
                       {"Rotation": self.inp(q2, ROTATION), "Rotate By": self.inp(q1, ROTATION)})
        return Val(ROTATION, o=n.out("Rotation"), field=q1.field or q2.field)

    def rot_apply(self, q, v):
        q, v = self.rot(q), self.coerce(v, VECTOR, "the vector to rotate")
        n = self.g.add("FunctionNodeRotateVector", {}, {"Vector": self.inp(v, VECTOR), "Rotation": self.inp(q, ROTATION)})
        return Val(VECTOR, o=n.out("Vector"), field=q.field or v.field)

    def rot_slerp(self, a, b, t):
        a, b = self.rot(a), self.rot(b)
        t = self.coerce(t, FLOAT, "the blend factor")
        n = self.g.add("ShaderNodeMix", {"data_type": "ROTATION", "clamp_factor": True},
                       {"Factor_Float": self.inp(t, FLOAT), "A_Rotation": self.inp(a, ROTATION),
                        "B_Rotation": self.inp(b, ROTATION)})
        return Val(ROTATION, o=n.out("Result_Rotation"), field=a.field or b.field or t.field)

    def axis_angle(self, axis, angle):
        axis = self.coerce(axis, VECTOR, "the axis")
        angle = self.coerce(angle, FLOAT, "the angle")
        n = self.g.add("FunctionNodeAxisAngleToRotation", {},
                       {"Axis": self.inp(axis, VECTOR), "Angle": self.inp(angle, FLOAT)})
        return Val(ROTATION, o=n.out("Rotation"), field=axis.field or angle.field)

    def euler_rot(self, v):
        v = self.coerce(v, VECTOR, "the Euler angles")
        if v.is_const:
            return Val(ROTATION, c=tuple(float(x) for x in v.c))
        n = self.g.add("FunctionNodeEulerToRotation", {}, {"Euler": self.inp(v, VECTOR)})
        return Val(ROTATION, o=n.out("Rotation"), field=v.field)

    def matrix_mul(self, first, then):
        """Matrix that applies ``first`` and then ``then``."""
        a, b = self.coerce(first, MATRIX), self.coerce(then, MATRIX)
        n = self.g.add("FunctionNodeMatrixMultiply", {}, {"Matrix": self.inp(b, MATRIX), "Matrix_001": self.inp(a, MATRIX)})
        return Val(MATRIX, o=n.out("Matrix"), field=a.field or b.field)

    def transform_point(self, v, m, node="FunctionNodeTransformPoint", sock="Vector"):
        v, m = self.coerce(v, VECTOR, "the position"), self.coerce(m, MATRIX, "the matrix")
        n = self.g.add(node, {}, {sock: self.inp(v, VECTOR), "Transform": self.inp(m, MATRIX)})
        return Val(VECTOR, o=n.out(sock), field=v.field or m.field)

    def xform_binop(self, op, l, r):
        if op is ast.Mult:
            if l.t == ROTATION and r.t == ROTATION:
                return self.rot_multiply(l, r)
            if l.t == ROTATION and r.t == VECTOR:
                return self.rot_apply(l, r)
            if l.t == VECTOR and r.t == ROTATION:
                return self.rot_apply(r, l)
            if l.t == MATRIX and r.t == MATRIX:
                return self.matrix_mul(l, r)        # VEX: a * b applies a first
            if l.t == VECTOR and r.t == MATRIX:
                return self.transform_point(l, r)
            if l.t == MATRIX and r.t == VECTOR:
                return self.transform_point(r, l)
        if op is ast.Div and l.t == MATRIX and r.t == MATRIX:
            return self.matrix_mul(l, self.f_invert_val(r))
        raise FormulaError(f"{type_word(l.t)} and {type_word(r.t)} can't be combined with that operator — "
                           f"rotations combine with *, matrices with * (v * m transforms a position)")

    # ── rotation functions ─────────────────────────────────────────────────
    def f_quaternion(self, node, name):
        vals = self.args(node, name, {1, 2})
        if len(vals) == 2:
            ang, axis = vals
            if ang.t == VECTOR and axis.t != VECTOR:      # quaternion(axis, angle) by mistake
                ang, axis = axis, ang
            return self.axis_angle(axis, ang)
        v = vals[0]
        if v.t == ROTATION:
            return v
        if v.t == MATRIX:
            return self.f_matpart_val(v, "Rotation")
        if v.t == VECTOR:          # angle-axis vector: axis * angle
            return self.axis_angle(v, self.vmath("LENGTH", v))
        raise FormulaError("quaternion() takes (angle, axis), an angle-axis vector or a matrix")

    def f_eulertoquaternion(self, node, name):
        vals = self.args(node, name, {1, 2})
        if len(vals) == 2:
            order = vals[1]
            if not (order.is_const and int(order.c) == 0):
                self.note(f"{name}(): Blender uses XYZ rotation order — the order argument is ignored")
        return self.euler_rot(vals[0])

    def f_quaterniontoeuler(self, node, name):
        vals = self.args(node, name, {1, 2})
        q = self.rot(vals[0])
        if q.is_const:
            return Val(VECTOR, c=q.c)
        n = self.g.add("FunctionNodeRotationToEuler", {}, {"Rotation": self.inp(q, ROTATION)})
        return Val(VECTOR, o=n.out("Euler"), field=q.field)

    def f_qrotate(self, node, name):
        q, v = self.args(node, name, {2})
        return self.rot_apply(q, v)

    def f_qmultiply(self, node, name):
        a, b = self.args(node, name, {2})
        return self.rot_multiply(a, b)

    def f_qinvert(self, node, name):
        (q,) = self.args(node, name, {1})
        return self.rot_invert(q)

    def f_slerp(self, node, name):
        a, b, t = self.args(node, name, {3})
        if a.t == VECTOR and b.t == VECTOR:
            return self.f_lerp_vals(a, b, t, name)
        return self.rot_slerp(a, b, t)

    def f_dihedral(self, node, name):
        a, b = [self.coerce(v, VECTOR, "dihedral()'s direction") for v in self.args(node, name, {2})]
        na, nb = self.vmath("NORMALIZE", a), self.vmath("NORMALIZE", b)
        axis = self.vmath("CROSS_PRODUCT", na, nb)
        cosang = self.math("MINIMUM", self.math("MAXIMUM", self.vmath("DOT_PRODUCT", na, nb), Val(FLOAT, c=-1.0)),
                           Val(FLOAT, c=1.0))
        angle = self.math("ARCCOSINE", cosang)
        # opposite directions: any axis perpendicular to a works
        alt = self.vmath("CROSS_PRODUCT", na, self.switch(
            self.compare("LESS_THAN", self.math("ABSOLUTE", self.sep(na, 0)), Val(FLOAT, c=0.9)),
            Val(VECTOR, c=(0.0, 1.0, 0.0)), Val(VECTOR, c=(1.0, 0.0, 0.0)), VECTOR))
        use_alt = self.compare("LESS_THAN", self.vmath("LENGTH", axis), Val(FLOAT, c=1e-6))
        axis = self.switch(use_alt, axis, alt, VECTOR)
        return self.axis_angle(axis, angle)

    def f_lookat(self, node, name):
        vals = self.args(node, name, {2, 3})
        frm, to = self.coerce(vals[0], VECTOR, "lookat()'s from"), self.coerce(vals[1], VECTOR, "lookat()'s to")
        up = self.coerce(vals[2], VECTOR, "lookat()'s up") if len(vals) == 3 else Val(VECTOR, c=(0.0, 0.0, 1.0))
        n = self.g.add("FunctionNodeAxesToRotation", {"primary_axis": "Z", "secondary_axis": "Y"},
                       {"Primary Axis": self.inp(self.vmath("SUBTRACT", frm, to), VECTOR),
                        "Secondary Axis": self.inp(up, VECTOR)})
        return Val(ROTATION, o=n.out("Rotation"), field=frm.field or to.field or up.field)

    def f_alignaxis(self, node, name):
        if len(node.args) not in (1, 2, 3):
            raise FormulaError('alignaxis() takes alignaxis(dir [, up, "z"])')
        d = self.coerce(self.expr(node.args[0]), VECTOR, "alignaxis()'s direction")
        axis = "Z"
        up = None
        if len(node.args) >= 2:
            up = self.coerce(self.expr(node.args[1]), VECTOR, "alignaxis()'s up")
        if len(node.args) == 3:
            axis = self.str_const(node.args[2], "alignaxis()'s axis").strip().upper()
            if axis not in ("X", "Y", "Z"):
                raise FormulaError('alignaxis(): the axis is "x", "y" or "z"')
        if up is None:
            n = self.g.add("FunctionNodeAlignRotationToVector", {"axis": axis, "pivot_axis": "AUTO"},
                           {"Factor": 1.0, "Vector": self.inp(d, VECTOR)})
            return Val(ROTATION, o=n.out("Rotation"), field=d.field)
        second = "Y" if axis != "Y" else "Z"
        n = self.g.add("FunctionNodeAxesToRotation", {"primary_axis": axis, "secondary_axis": second},
                       {"Primary Axis": self.inp(d, VECTOR), "Secondary Axis": self.inp(up, VECTOR)})
        return Val(ROTATION, o=n.out("Rotation"), field=d.field or up.field)

    def f_qaxisangle(self, node, name):
        (q,) = self.args(node, name, {1})
        q = self.rot(q)
        n = self.g.add("FunctionNodeRotationToAxisAngle", {}, {"Rotation": self.inp(q, ROTATION)})
        out = FUNCS[name].data["out"]
        return Val(VECTOR if out == "Axis" else FLOAT, o=n.out(out), field=q.field)

    # ── matrices ───────────────────────────────────────────────────────────
    def f_ident(self, node, name):
        self.args(node, name, {0})
        return Val(MATRIX, c=("ident",))

    def f_maketransform(self, node, name):
        vals = self.args(node, name, {1, 2, 3, 5})
        if len(vals) == 5:      # VEX: maketransform(trs, xyz, t, r_degrees, s)
            for v, what in ((vals[0], "trs"), (vals[1], "xyz")):
                if not (v.is_const and int(v.c) == 0):
                    self.note(f"maketransform(): only the default {what} order (0) is supported")
            t, r, s = vals[2], vals[3], vals[4]
            r = self.vmath("SCALE", self.coerce(r, VECTOR), scale=Val(FLOAT, c=math.pi / 180.0))
        else:
            t = vals[0]
            r = vals[1] if len(vals) > 1 else Val(ROTATION, c=(0.0, 0.0, 0.0))
            s = vals[2] if len(vals) > 2 else Val(VECTOR, c=(1.0, 1.0, 1.0))
        t = self.coerce(t, VECTOR, "the translation")
        r = r if r.t == ROTATION else self.euler_rot(r)
        s = self.coerce(s, VECTOR, "the scale")
        n = self.g.add("FunctionNodeCombineTransform", {},
                       {"Translation": self.inp(t, VECTOR), "Rotation": self.inp(r, ROTATION),
                        "Scale": self.inp(s, VECTOR)})
        return Val(MATRIX, o=n.out("Transform"), field=t.field or r.field or s.field)

    def f_matpart_val(self, m, out):
        m = self.coerce(m, MATRIX, "the matrix")
        n = self.g.add("FunctionNodeSeparateTransform", {}, {"Transform": self.inp(m, MATRIX)})
        return Val(ROTATION if out == "Rotation" else VECTOR, o=n.out(out), field=m.field)

    def f_matpart(self, node, name):
        (m,) = self.args(node, name, {1})
        return self.f_matpart_val(m, FUNCS[name].data["out"])

    def f_cracktransform(self, node, name):
        if len(node.args) == 5:     # VEX: cracktransform(trs, xyz, c, pivot, m)
            m = self.expr(node.args[4])
            cnode = node.args[2]
        elif len(node.args) == 2:
            m = self.expr(node.args[0])
            cnode = node.args[1]
        else:
            raise FormulaError("cracktransform() takes cracktransform(m, c) or the VEX 5-argument form")
        if not (isinstance(cnode, ast.Constant) and cnode.value in (0, 1, 2)):
            raise FormulaError("cracktransform(): c must be 0 (translate), 1 (rotate) or 2 (scale)")
        c = int(cnode.value)
        if c == 0:
            return self.f_matpart_val(m, "Translation")
        if c == 2:
            return self.f_matpart_val(m, "Scale")
        rot = self.f_matpart_val(m, "Rotation")
        n = self.g.add("FunctionNodeRotationToEuler", {}, {"Rotation": self.inp(rot, ROTATION)})
        return self.vmath("SCALE", Val(VECTOR, o=n.out("Euler"), field=rot.field), scale=Val(FLOAT, c=180.0 / math.pi))

    def f_invert_val(self, v):
        if v.t == ROTATION:
            return self.rot_invert(v)
        m = self.coerce(v, MATRIX, "invert()'s argument")
        n = self.g.add("FunctionNodeInvertMatrix", {}, {"Matrix": self.inp(m, MATRIX)})
        return Val(MATRIX, o=n.out("Matrix"), field=m.field)

    def f_invert(self, node, name):
        (v,) = self.args(node, name, {1})
        return self.f_invert_val(v)

    def f_matunary(self, node, name):
        (m,) = self.args(node, name, {1})
        d = FUNCS[name].data
        m = self.coerce(m, MATRIX, f"{name}()'s argument")
        n = self.g.add(d["node"], {}, {"Matrix": self.inp(m, MATRIX)})
        return Val(d["t"], o=n.out(d["out"]), field=m.field)

    def f_transform(self, node, name):
        v, m = self.args(node, name, {2})
        if v.t == MATRIX and m.t == VECTOR:
            v, m = m, v
        d = FUNCS[name].data
        return self.transform_point(v, m, d["node"], d["sock"])

    # ── colour ─────────────────────────────────────────────────────────────
    def f_colorspace(self, node, name):
        (v,) = self.args(node, name, {1})
        d = FUNCS[name].data
        v = self.coerce(v, VECTOR, f"{name}()'s colour")
        if d["to_rgb"]:
            parts = [self.sep(v, i) for i in range(3)]
            n = self.g.add("FunctionNodeCombineColor", {"mode": d["mode"]},
                           {"Red": self.inp(parts[0], FLOAT), "Green": self.inp(parts[1], FLOAT),
                            "Blue": self.inp(parts[2], FLOAT)})
            return Val(VECTOR, o=n.out("Color"), field=v.field)
        n = self.g.add("FunctionNodeSeparateColor", {"mode": d["mode"]}, {"Color": self.inp(v, VECTOR)})
        return self.combine([Val(FLOAT, o=n.out(k), field=v.field) for k in ("Red", "Green", "Blue")])

    def f_luminance(self, node, name):
        (v,) = self.args(node, name, {1})
        return self.vmath("DOT_PRODUCT", self.coerce(v, VECTOR, "luminance()'s colour"),
                          Val(VECTOR, c=(0.2126, 0.7152, 0.0722)))

    def f_blackbody(self, node, name):
        vals = self.args(node, name, {1, 2})
        temp = self.coerce(vals[0], FLOAT, "the temperature")
        n = self.g.add("ShaderNodeBlackbody", {}, {"Temperature": self.inp(temp, FLOAT)})
        col = Val(VECTOR, o=n.out("Color"), field=temp.field)
        if len(vals) == 2:
            col = self.vmath("SCALE", col, scale=vals[1])
        return col

    def f_colormix(self, node, name):
        if len(node.args) not in (3, 4):
            raise FormulaError('colormix() takes colormix(a, b, t [, "multiply"])')
        a = self.coerce(self.expr(node.args[0]), VECTOR, "colormix()'s first colour")
        b = self.coerce(self.expr(node.args[1]), VECTOR, "colormix()'s second colour")
        t = self.coerce(self.expr(node.args[2]), FLOAT, "colormix()'s factor")
        mode = "MIX"
        if len(node.args) == 4:
            word = self.str_const(node.args[3], "colormix()'s mode").strip().lower().replace(" ", "").replace("_", "")
            if word not in _BLEND:
                raise FormulaError(f"colormix(): unknown mode '{word}' — {', '.join(sorted(_BLEND))}")
            mode = _BLEND[word]
        n = self.g.add("ShaderNodeMix", {"data_type": "RGBA", "blend_type": mode, "clamp_factor": True},
                       {"Factor_Float": self.inp(t, FLOAT), "A_Color": self.inp(a, VECTOR),
                        "B_Color": self.inp(b, VECTOR)})
        return Val(VECTOR, o=n.out("Result_Color"), field=a.field or b.field or t.field)
