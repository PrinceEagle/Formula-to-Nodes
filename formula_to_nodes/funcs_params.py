# SPDX-License-Identifier: GPL-3.0-or-later
"""Parameters (group inputs shown on the node and the modifier) and ramps."""

import ast

from .core import (FLOAT, INT, BOOL, VECTOR, STRING, ROTATION, OBJECT, COLLECTION, MATERIAL, IMAGE, SOUND,
                   Val, FUNCS, reg, FormulaError, quat_to_euler)

_KW = ("default", "min", "max", "tip", "help")
reg("chf ch", 'NAME("name", default, min=, max=, tip=)', 'float slider; "Folder/name" groups it in a panel',
    "Parameters", "f_param", t=FLOAT, keywords=True)
reg("chi", 'chi("name", default, min=, max=, tip=)', "integer field", "Parameters", "f_param", t=INT,
    keywords=True)
reg("chv ch3", 'NAME("name", {x, y, z})', "vector field", "Parameters", "f_param", t=VECTOR, keywords=True)
reg("chb", 'chb("name", default)', "checkbox", "Parameters", "f_param", t=BOOL, keywords=True)
reg("chs", 'chs("name", "default")', "text field", "Parameters", "f_param", t=STRING, keywords=True)
reg("chp ch4", 'NAME("name", {x, y, z, w})', "rotation field (quaternion default)", "Parameters", "f_param",
    t=ROTATION, keywords=True)
reg("chobj", 'chobj("name")', "object picker (use as geometry: point(chobj(\"target\"), ...), instance(...))",
    "Parameters", "f_param", t=OBJECT, keywords=True)
reg("chcoll", 'chcoll("name")', "collection picker (instance(chcoll(\"rocks\")))", "Parameters", "f_param",
    t=COLLECTION, keywords=True)
reg("chmat", 'chmat("name")', "material picker (setmaterial(chmat(\"paint\")))", "Parameters", "f_param",
    t=MATERIAL, keywords=True)
reg("chimg", 'chimg("name")', "image picker (texture(chimg(\"map\"), uv))", "Parameters", "f_param", t=IMAGE,
    keywords=True)
reg("chsound", 'chsound("name")', "sound picker (spectrum(chsound(\"music\"), 60, 250))", "Parameters",
    "f_param", t=SOUND, keywords=True, feature="sound")
reg("chramp", 'chramp("name", x [, "preset"])',
    "curve you shape in the sidebar (assign to a vector for a colour ramp); presets: linear smooth ease_in "
    "ease_out bell valley spike plateau steps sine invert", "Parameters", "f_chramp")
reg("colorramp", 'colorramp("name", x [, "preset"])',
    "colour ramp you edit in the sidebar; presets: grayscale fire heat rainbow viridis magma terrain ice",
    "Parameters", "f_chramp", color=True)

FLOAT_RAMP_PRESETS = ("linear", "smooth", "ease_in", "ease_out", "bell", "valley", "spike", "plateau", "steps",
                      "sine", "invert")
COLOR_RAMP_PRESETS = ("grayscale", "fire", "heat", "rainbow", "viridis", "magma", "terrain", "ice")


class ParamFuncs:
    def f_param(self, node, name):
        t = FUNCS[name].data["t"]
        if not node.args or not (isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            raise FormulaError(f'{name}() needs a name in quotes, e.g. {name}("strength", 0.5)')
        pname = node.args[0].value.strip()
        rest = node.args[1:]
        kw = {k.arg: k.value for k in node.keywords}
        for k in kw:
            if k not in _KW:
                raise FormulaError(f"{name}() doesn't know '{k}=' — use default=, min=, max= or tip=")

        def const_of(expr_node, want, what):
            v = self.coerce(self.expr(expr_node), want, what)
            if not v.is_const:
                raise FormulaError(f"{name}(\"{pname}\"): {what} must be a plain value")
            return v.c

        tip = ""
        for key in ("tip", "help"):
            if key in kw:
                tip = const_of(kw[key], STRING, "the tooltip")
        default = None
        if t in (OBJECT, COLLECTION, MATERIAL, IMAGE, SOUND):
            if rest or "default" in kw:
                raise FormulaError(f"{name}() only takes a name — pick the {t.lower()} on the modifier")
        elif t == ROTATION:
            if len(rest) == 4:
                default = quat_to_euler(*[float(const_of(e, FLOAT, "the default")) for e in rest])
            elif len(rest) == 1:
                v = self.expr(rest[0])
                if v.t == ROTATION and v.is_const:
                    default = v.c
                else:
                    raise FormulaError(f'{name}() takes a quaternion default: {name}("rot", {{0, 0, 0, 1}})')
            elif rest:
                raise FormulaError(f'{name}() takes a quaternion default: {name}("rot", {{0, 0, 0, 1}})')
        elif t == VECTOR and len(rest) == 3:
            default = tuple(const_of(e, FLOAT, "the default") for e in rest)
        elif len(rest) == 1:
            default = const_of(rest[0], t, "the default")
        elif len(rest) > 1:
            raise FormulaError(f'{name}() takes a name and one default: {FUNCS[name].sig}')
        if "default" in kw:
            default = const_of(kw["default"], t, "the default")
        if default is None and t in (FLOAT, INT, BOOL, VECTOR, STRING, ROTATION):
            default = {FLOAT: 0.0, INT: 0, BOOL: False, VECTOR: (0.0, 0.0, 0.0), STRING: "",
                       ROTATION: (0.0, 0.0, 0.0)}[t]
        vmin = const_of(kw["min"], FLOAT, "min") if "min" in kw else None
        vmax = const_of(kw["max"], FLOAT, "max") if "max" in kw else None
        if t not in (FLOAT, INT) and (vmin is not None or vmax is not None):
            raise FormulaError(f"{name}() doesn't support min/max")
        return self.param(pname, t, default, vmin, vmax, explicit=True, description=tip)

    def f_chramp(self, node, name):
        color = FUNCS[name].data.get("color", False) or self.hinted(node) == VECTOR
        if len(node.args) not in (2, 3) or not (isinstance(node.args[0], ast.Constant)
                                                and isinstance(node.args[0].value, str)):
            raise FormulaError(f'{name}() takes {name}("name", x [, "preset"])')
        rname = node.args[0].value.strip()
        if not rname:
            raise FormulaError(f"{name}() needs a name")
        x = self.coerce(self.expr(node.args[1]), FLOAT, f"{name}()'s position")
        presets = COLOR_RAMP_PRESETS if color else FLOAT_RAMP_PRESETS
        preset = presets[0] if not color else "grayscale"
        if not color:
            preset = "linear"
        if len(node.args) == 3:
            preset = self.str_const(node.args[2], f"{name}()'s preset").strip().lower().replace(" ", "_")
            if preset not in presets:
                raise FormulaError(f"{name}(): unknown preset '{preset}' — {', '.join(presets)}")
        kind = "color" if color else "float"
        prev = next((r for r in self.ramps if r[0] == rname), None)
        if prev is not None and prev[1] != kind:
            raise FormulaError(f"ramp '{rname}' is used as both a float ramp and a colour ramp")
        if prev is None:
            self.ramps.append((rname, kind))
        else:
            self.note(f"ramp '{rname}' is used more than once — each use gets its own curve; read it into a "
                      f"variable once if they should match")
        label = f"Ramp: {rname}"
        if color:
            n = self.g.add("ShaderNodeValToRGB", {}, {"Fac": self.inp(x, FLOAT)}, pure=False, label=label,
                           ramp=("color", rname, preset))
            return Val(VECTOR, o=n.out("Color"), field=x.field)
        n = self.g.add("ShaderNodeFloatCurve", {}, {"Factor": 1.0, "Value": self.inp(x, FLOAT)}, pure=False,
                       label=label, ramp=("float", rname, preset))
        return Val(FLOAT, o=n.out("Value"), field=x.field)
