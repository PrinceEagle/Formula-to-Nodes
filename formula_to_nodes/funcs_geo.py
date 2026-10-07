# SPDX-License-Identifier: GPL-3.0-or-later
"""Geometry functions: other inputs, attribute lookups, sampling, topology,
aggregates, detail attributes, instances, volumes, images and sound."""

import ast
import re

from .core import (FLOAT, INT, BOOL, VECTOR, STRING, ROTATION, MATRIX, COLOR, FLOAT2, OBJECT, COLLECTION,
                   IMAGE, SOUND, VOID, READ_DTYPE, ATTR_DTYPE, SOCKET_DTYPE, Val, FUNCS, reg, FormulaError, type_word, Out)
from .lang import split_attr_placeholder, RUNOVER_DOMAINS

# ── lookups on this or another geometry ─────────────────────────────────────
reg("point", 'point(geo, "attr", ptnum)', 'attribute of another point, e.g. point(0, "P", i) or point(1, "Cd", i)',
    "Geometry", "f_point", domain="POINT")
reg("prim", 'prim(geo, "attr", primnum)', "attribute of a face (prim)", "Geometry", "f_point", domain="FACE")
reg("vertex", 'vertex(geo, "attr", vtxnum)', "attribute of a face corner (vertex)", "Geometry", "f_point",
    domain="CORNER")
reg("detail", 'detail(geo, "attr" [, default])', 'detail (whole-geometry) attribute, e.g. detail(0, "f@total")',
    "Geometry", "f_detail", feature="geometry_bundles")
reg("nearpoint", "nearpoint(geo, pos [, maxdist])",
    "nearest point index: on input 0 the nearest OTHER point; -1 if beyond maxdist", "Geometry", "f_nearpoint")
reg("npoints", "npoints(geo)", "number of points", "Geometry", "f_count", domain="POINT")
reg("nprimitives", "nprimitives(geo)", "number of faces", "Geometry", "f_count", domain="FACE")
reg("nvertices", "nvertices(geo)", "number of face corners", "Geometry", "f_count", domain="CORNER")
reg("nedges", "nedges(geo)", "number of edges", "Geometry", "f_count", domain="EDGE")
reg("ncurves", "ncurves(geo)", "number of curves", "Geometry", "f_count", domain="CURVE")
reg("relbbox", "relbbox(geo, pos)", "position inside the bounding box, 0..1 per axis", "Geometry", "f_bbox", which="rel")
reg("getbbox_min", "getbbox_min(geo)", "bounding box minimum", "Geometry", "f_bbox", which="min")
reg("getbbox_max", "getbbox_max(geo)", "bounding box maximum", "Geometry", "f_bbox", which="max")
reg("getbbox_center", "getbbox_center(geo)", "bounding box center", "Geometry", "f_bbox", which="center")
reg("getbbox_size", "getbbox_size(geo)", "bounding box size", "Geometry", "f_bbox", which="size")
reg("haspointattrib", 'haspointattrib(geo, "name")', "does the attribute exist", "Geometry", "f_hasattrib")
reg("hasprimattrib", 'hasprimattrib(geo, "name")', "does the attribute exist", "Geometry", "f_hasattrib")
reg("hasvertexattrib", 'hasvertexattrib(geo, "name")', "does the attribute exist", "Geometry", "f_hasattrib")
reg("hasdetailattrib", 'hasdetailattrib(geo, "name")', "does the detail attribute exist", "Geometry",
    "f_hasdetail", feature="geometry_bundles")
reg("curvepos", "curvepos(geo, curve, t)", "position at 0..1 along a curve", "Geometry", "f_curvesample", out="Position")
reg("curvetangent", "curvetangent(geo, curve, t)", "tangent at 0..1 along a curve", "Geometry", "f_curvesample",
    out="Tangent")

# ── sampling surfaces, rays, uvs, volumes, images, sound ────────────────────
reg("xyzdist", "xyzdist(geo, pos [, prim_out, uv_out])", "distance to the closest surface point of geo",
    "Sampling", "f_xyzdist", target="FACES")
reg("edgedist", "edgedist(geo, pos)", "distance to the closest edge of geo", "Sampling", "f_xyzdist", target="EDGES")
reg("pointdist", "pointdist(geo, pos)", "distance to the closest point of geo", "Sampling", "f_xyzdist",
    target="POINTS")
reg("minpos", "minpos(geo, pos)", "closest surface position on geo", "Sampling", "f_minpos")
reg("primuv", 'primuv(geo, "attr", pos)', "attribute interpolated at the closest surface point (Sample Nearest Surface)",
    "Sampling", "f_primuv")
reg("uvsample", 'uvsample(geo, "attr", "uvmap", uv)', "attribute at a UV coordinate of geo", "Sampling", "f_uvsample")
reg("intersect", "intersect(geo, orig, dir [, pos_out, uvw_out])",
    "ray cast (max distance = length(dir)); returns the hit face or -1", "Sampling", "f_intersect")
reg("rayhit", "rayhit(geo, orig, dir)", "does the ray hit geo (max distance = length(dir))", "Sampling",
    "f_ray", out="Is Hit")
reg("raypos", "raypos(geo, orig, dir)", "hit position (orig + dir when it misses)", "Sampling", "f_ray",
    out="Hit Position")
reg("raynormal", "raynormal(geo, orig, dir)", "surface normal at the hit", "Sampling", "f_ray", out="Hit Normal")
reg("raydist", "raydist(geo, orig, dir)", "distance to the hit", "Sampling", "f_ray", out="Hit Distance")
reg("volumesample", 'volumesample(geo, "grid", pos)', "float volume grid value at pos", "Sampling",
    "f_volumesample", t=FLOAT)
reg("volumesamplev", 'volumesamplev(geo, "grid", pos)', "vector volume grid value at pos", "Sampling",
    "f_volumesample", t=VECTOR)
reg("volumegradient", 'volumegradient(geo, "grid", pos)', "gradient of a float grid at pos", "Sampling",
    "f_volumegradient")
reg("texture colormap", "NAME(image, uv) or NAME(image, u, v)", 'image colour at a UV, image = chimg("name")',
    "Sampling", "f_texture")
reg("spectrum", "spectrum(sound, low_hz, high_hz [, time])",
    'loudness of a frequency band, sound = chsound("music") — audio reactive', "Sampling", "f_spectrum",
    feature="sound")

# ── topology (input 0, meshes and curves) ───────────────────────────────────
reg("neighbourcount neighborcount", "NAME(geo, pt)", "number of points connected to pt by an edge", "Topology",
    "f_neighbourcount")
reg("neighbour neighbor", "NAME(geo, pt, n)", "the n-th point connected to pt (-1 if none)", "Topology",
    "f_neighbour")
reg("primpoint", "primpoint(geo, prim, n)", "the n-th point of a face (-1 if none)", "Topology", "f_primpoint")
reg("primvertexcount", "primvertexcount(geo, prim)", "number of corners of a face (points of a curve)",
    "Topology", "f_primvertexcount")
reg("vertexpoint", "vertexpoint(geo, vtx)", "point of a face corner", "Topology", "f_vertexpoint")
reg("vertexprim", "vertexprim(geo, vtx)", "face of a face corner", "Topology", "f_vertexprim", out="Face Index")
reg("vertexprimindex", "vertexprimindex(geo, vtx)", "corner number inside its face", "Topology",
    "f_vertexprim", out="Index in Face")
reg("curvepoint", "curvepoint(geo, curve, n)", "the n-th point of a curve", "Topology", "f_curvepoint")
reg("pointcurve", "pointcurve(geo, pt)", "the curve a point belongs to", "Topology", "f_pointcurve")

# ── aggregates over the current domain ──────────────────────────────────────
for _n, _op, _doc in [("sumof", "sum", "sum over all elements (or per group)"),
                      ("avgof meanof", "mean", "average over all elements (or per group)"),
                      ("minof", "min", "smallest value (or per group)"), ("maxof", "max", "largest value (or per group)"),
                      ("medianof", "median", "median (or per group)"),
                      ("stdevof", "stdev", "standard deviation (or per group)"),
                      ("varianceof", "variance", "variance (or per group)"),
                      ("countof", "count", "how many elements the condition is true for (or per group)")]:
    reg(_n, "NAME(value [, group])", _doc, "Aggregates", "f_aggregate", op=_op)
reg("accumulate", "accumulate(value [, group])", "running total in element order", "Aggregates", "f_accumulate")
reg("blur", "blur(value, iterations [, weight])", "averages with mesh/curve neighbours (smoothing)", "Aggregates",
    "f_blur")
reg("atindex", "atindex(value, index)", "evaluates an expression at another element of the current domain",
    "Aggregates", "f_atindex")
reg("ondomain", 'ondomain(value, "domain")', 'computes value per "point"/"prim"/"edge"/"vertex" and '
    "interpolates it to the current domain", "Aggregates", "f_ondomain")

# ── effects that write elsewhere ────────────────────────────────────────────
reg("setdetailattrib", 'setdetailattrib(geo, "name", value [, "set"|"add"|"min"|"max"|"mean"])',
    "write a detail attribute; from per-element values use add/min/max/mean", "Effects", "f_setdetailattrib",
    feature="geometry_bundles", effect=True)
reg("setpointattrib", 'setpointattrib(geo, "name", ptnum, value [, "set"|"add"])',
    "write a point attribute (this point, or one fixed point)", "Effects", "f_setelemattrib", effect=True,
    domain="POINT")
reg("setprimattrib", 'setprimattrib(geo, "name", primnum, value [, "set"|"add"])',
    "write a face attribute (this face, or one fixed face)", "Effects", "f_setelemattrib", effect=True,
    domain="FACE")


def _domain_word(d):
    return {"POINT": "point", "FACE": "prim", "CORNER": "vertex", "EDGE": "edge", "CURVE": "curve",
            "INSTANCE": "instance", "LAYER": "layer"}.get(d, d.lower())


class GeoFuncs:
    # ── geometry arguments ─────────────────────────────────────────────────
    def geo_arg(self, node, fname):
        """(geometry Out, is_current_geometry) for VEX geometry arguments:
        0 = this geometry, 1..9 = the "Input N" object inputs, or chobj("name")."""
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                and not isinstance(node.value, bool):
            n = int(node.value)
            if n == 0:
                return self.ctx.geo, True
            if not 1 <= n <= 9:
                raise FormulaError(f"{fname}(): input numbers go from 0 (this geometry) to 9")
            return self.opinput_geo(n), False
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            m = re.fullmatch(r"\s*opinput:(\d)\s*", node.value)
            if m:
                return self.geo_arg(ast.Constant(int(m.group(1))), fname)
            raise FormulaError(f'{fname}(): geometry paths aren\'t supported — use 0, 1, ... or chobj("name")')
        v = self.expr(node)
        if v.t == OBJECT:
            return self.object_geometry(v), False
        if v.t == COLLECTION:
            ci = self.g.add("GeometryNodeCollectionInfo", {"transform_space": "RELATIVE"},
                            {"Collection": v.o, "Separate Children": False, "Reset Children": False})
            rz = self.g.add("GeometryNodeRealizeInstances", {}, {"Geometry": ci.out("Instances")})
            return rz.out("Geometry"), False
        raise FormulaError(f'{fname}()\'s first argument is the geometry: 0 (this one), 1 (the "Input 1" '
                           f'object) or chobj("name")')

    def opinput_geo(self, n):
        v = self.param(f"Input {n}", OBJECT, None, explicit=True,
                       description=f"Object used as input {n}: point({n}, ...), @opinput{n}_name, xyzdist({n}, ...)")
        return self.object_geometry(v)

    def object_geometry(self, v):
        n = self.g.add("GeometryNodeObjectInfo", {"transform_space": "RELATIVE"},
                       {"Object": v.o, "As Instance": False})
        return n.out("Geometry")

    def _is_current_index(self, v, domain="POINT"):
        return (not v.is_const and v.o.node.idname == "GeometryNodeInputIndex" and v.o.sock == "Index"
                and self.field_domain() == domain)

    def _attr_spec(self, text, fname, default_prefix="f"):
        """'P' / 'v@P' / 'f@mask' → (read node idname or None, socket, type, storage, name)."""
        text = text.strip()
        prefix, raw = "", text
        if "@" in text:
            prefix, _, raw = text.partition("@")
            if prefix not in ("", "f", "i", "b", "v", "s", "p", "c", "u", "3", "4"):
                raise FormulaError(f"{fname}(): unknown prefix in '{text}'")
        if not raw.isidentifier():
            raise FormulaError(f"{fname}(): '{text}' isn't a valid attribute name")
        from .compiler import (VEX_ALIASES, BUILTIN_ATTRS, NAMED_ATTR_TYPES, NAMED_STORE, NAMED_STORAGE_NAME)
        from .core import PREFIX_TYPE, PREFIX_STORE
        name = VEX_ALIASES.get(raw, raw)
        if name in BUILTIN_ATTRS:
            node, sock, t, _ = BUILTIN_ATTRS[name]
            return node, sock, t, None, name
        if prefix:
            t = PREFIX_TYPE[prefix]
        elif name in NAMED_ATTR_TYPES:
            t = NAMED_ATTR_TYPES[name]
        elif name in self.attr_info:
            t = self.attr_info[name][0]
        else:
            t = PREFIX_TYPE[default_prefix]
        if t == STRING:
            self.require("string_fields")
        store = PREFIX_STORE.get(prefix) or NAMED_STORE.get(name)
        if name in self.attr_info and self.attr_info[name][1] in (COLOR, FLOAT2):
            store = self.attr_info[name][1]
        return None, None, t, store, NAMED_STORAGE_NAME.get(name, name)

    def _attr_field(self, spec):
        node, sock, t, store, name = spec
        if node:
            return Val(t, o=self.g.add(node).out(sock), field=True)
        dtype = READ_DTYPE[store] if store in (COLOR, FLOAT2) else READ_DTYPE[t]
        n = self.g.add("GeometryNodeInputNamedAttribute", {"data_type": dtype}, {"Name": name})
        return Val(t, o=n.out("Attribute"), field=True)

    def sample_index(self, geo, value, index, domain, t, store=None):
        dtype = READ_DTYPE[store] if store in (COLOR, FLOAT2) else READ_DTYPE[t]
        n = self.g.add("GeometryNodeSampleIndex", {"data_type": dtype, "domain": domain, "clamp": False},
                       {"Geometry": geo, "Value": self.inp(value, t), "Index": self.inp(index, INT)})
        return Val(t, o=n.out("Value"), field=index.field)

    def read_opinput_attr(self, a):
        geo = self.opinput_geo(a.input)
        inner = a.inner
        if inner.kind in ("index_of", "count", "detail", "const", "inst_xform"):
            raise FormulaError(f"@opinput{a.input}_{inner.name} can only read point/prim attributes")
        if inner.node:
            value = Val(inner.t, o=self.g.add(inner.node).out(inner.sock), field=True)
        else:
            store = inner.store
            dtype = READ_DTYPE[store] if store in (COLOR, FLOAT2) else READ_DTYPE[inner.t]
            n = self.g.add("GeometryNodeInputNamedAttribute", {"data_type": dtype}, {"Name": inner.name})
            value = Val(inner.t, o=n.out("Attribute"), field=True)
        dom = self.field_domain()
        idx = Val(INT, o=self.g.add("GeometryNodeInputIndex").out("Index"), field=True)
        return self.sample_index(geo, value, idx, dom, inner.t, inner.store)

    # ── point() / prim() / vertex() ────────────────────────────────────────
    def f_point(self, node, name):
        domain = FUNCS[name].data["domain"]
        if len(node.args) != 3:
            raise FormulaError(f'{name}() takes 3 arguments: {name}(0, "attr", index)')
        g, a, idx = node.args
        geo, _ = self.geo_arg(g, name)
        text = self.str_const(a, f"{name}()'s attribute name")
        spec = self._attr_spec(text, name)
        value = self._attr_field(spec)
        index = self.coerce(self.expr(idx), INT, f"{name}()'s index")
        return self.sample_index(geo, value, index, domain, spec[2], spec[3])

    def f_detail(self, node, name):
        if len(node.args) not in (2, 3):
            raise FormulaError('detail() takes 2 or 3 arguments: detail(0, "name" [, default])')
        geo, is_self = self.geo_arg(node.args[0], name)
        text = self.str_const(node.args[1], "detail()'s attribute name")
        prefix, _, raw = text.strip().rpartition("@")
        from .core import PREFIX_TYPE
        if prefix and prefix not in PREFIX_TYPE:
            raise FormulaError(f"detail(): unknown prefix in '{text}'")
        t = PREFIX_TYPE[prefix] if prefix else self.detail_attrs.get(raw, FLOAT)
        item, exists = self.bundle_item(geo, raw, t)
        if len(node.args) == 3:
            default = self.coerce(self.expr(node.args[2]), t, "detail()'s default")
            return self.switch(exists, default, item, t)
        return item

    def f_hasdetail(self, node, name):
        if len(node.args) != 2:
            raise FormulaError('hasdetailattrib() takes 2 arguments: hasdetailattrib(0, "name")')
        geo, _ = self.geo_arg(node.args[0], name)
        text = self.str_const(node.args[1], "the attribute name")
        _item, exists = self.bundle_item(geo, text.rpartition("@")[2], FLOAT)
        return exists

    def f_hasattrib(self, node, name):
        if len(node.args) != 2:
            raise FormulaError(f'{name}() takes 2 arguments: {name}(0, "name")')
        geo, is_self = self.geo_arg(node.args[0], name)
        text = self.str_const(node.args[1], "the attribute name").strip()
        from .compiler import VEX_ALIASES, BUILTIN_ATTRS
        raw = text.rpartition("@")[2]
        if VEX_ALIASES.get(raw, raw) in BUILTIN_ATTRS:
            return Val(BOOL, c=True)
        n = self.g.add("GeometryNodeInputNamedAttribute", {"data_type": "FLOAT"}, {"Name": raw})
        exists = Val(BOOL, o=n.out("Exists"), field=True)
        if is_self:
            return exists
        return self.sample_index(geo, exists, Val(INT, c=0), "POINT", BOOL)

    def f_nearpoint(self, node, name):
        args = list(node.args)
        if not args:
            n = self.g.add("GeometryNodeIndexOfNearest", {}, {})
            return Val(INT, o=n.out("Index"), field=True)
        if len(args) == 1:
            args = [ast.Constant(0)] + args
        if len(args) not in (2, 3):
            raise FormulaError("nearpoint() takes nearpoint(geo, pos [, maxdist])")
        geo, is_self = self.geo_arg(args[0], name)
        pos = self.coerce(self.expr(args[1]), VECTOR, "nearpoint()'s position")
        if is_self:
            n = self.g.add("GeometryNodeIndexOfNearest", {}, {"Position": self.inp(pos, VECTOR)})
            idx = Val(INT, o=n.out("Index"), field=True)
        else:
            n = self.g.add("GeometryNodeSampleNearest", {"domain": "POINT"},
                           {"Geometry": geo, "Sample Position": self.inp(pos, VECTOR)})
            idx = Val(INT, o=n.out("Index"), field=pos.field)
        if len(args) == 3:
            maxd = self.coerce(self.expr(args[2]), FLOAT, "nearpoint()'s max distance")
            p = self.sample_index(geo, Val(VECTOR, o=self.g.add("GeometryNodeInputPosition").out("Position"),
                                           field=True), idx, "POINT", VECTOR)
            far = self.compare("GREATER_THAN", self.vmath("DISTANCE", p, pos), maxd)
            idx = self.switch(far, idx, Val(INT, c=-1), INT)
        return idx

    def f_count(self, node, name):
        domain = FUNCS[name].data["domain"]
        if len(node.args) > 1:
            raise FormulaError(f"{name}() takes the geometry: {name}(0)")
        geo = self.ctx.geo
        if node.args:
            geo, _ = self.geo_arg(node.args[0], name)
        return self.count_elements(domain, geo)

    def f_bbox(self, node, name):
        which = FUNCS[name].data["which"]
        args = list(node.args)
        geo = self.ctx.geo
        if which == "rel":
            if len(args) == 2:
                geo, _ = self.geo_arg(args[0], name)
                args = args[1:]
            if len(args) != 1:
                raise FormulaError("relbbox() takes relbbox(0, v@position)")
        else:
            if len(args) == 1:
                geo, _ = self.geo_arg(args[0], name)
            elif args:
                raise FormulaError(f"{name}() takes the geometry: {name}(0)")
        bb = self.g.add("GeometryNodeBoundBox", {}, {"Geometry": geo})
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

    def f_curvesample(self, node, name):
        if len(node.args) != 3:
            raise FormulaError(f"{name}() takes {name}(geo, curve, t)")
        geo, _ = self.geo_arg(node.args[0], name)
        curve = self.coerce(self.expr(node.args[1]), INT, f"{name}()'s curve")
        t = self.coerce(self.expr(node.args[2]), FLOAT, f"{name}()'s position along the curve")
        n = self.g.add("GeometryNodeSampleCurve", {"mode": "FACTOR", "use_all_curves": False, "data_type": "FLOAT"},
                       {"Curves": geo, "Factor": self.inp(t, FLOAT), "Curve Index": self.inp(curve, INT)})
        return Val(VECTOR, o=n.out(FUNCS[name].data["out"]), field=curve.field or t.field)

    # ── sampling ───────────────────────────────────────────────────────────
    def assign_out(self, arg, val, fname):
        """Assign to a VEX out-parameter (a variable or an attribute)."""
        if not isinstance(arg, ast.Name):
            raise FormulaError(f"{fname}(): output arguments must be variable names")
        info = split_attr_placeholder(arg.id)
        if info:
            attr = self.resolve_attr(*info, write=True)
            self._emit_write(attr, value=self.coerce(val, attr.t))
            return
        sc, b = self.lookup(arg.id)
        if b is None:
            self._check_new_name(arg.id)
            self._declare(arg.id, val.t, val)
        else:
            self._check_assignable(arg.id, sc)
            self.set_local(arg.id, b.t, self.coerce(val, b.t, f"'{arg.id}'"))

    def f_xyzdist(self, node, name):
        target = FUNCS[name].data["target"]
        allowed = {2, 3, 4} if name == "xyzdist" else {2}
        if len(node.args) not in allowed:
            raise FormulaError(f"{name}() takes {FUNCS[name].sig}")
        geo, _ = self.geo_arg(node.args[0], name)
        pos = self.coerce(self.expr(node.args[1]), VECTOR, f"{name}()'s position")
        n = self.g.add("GeometryNodeProximity", {"target_element": target},
                       {"Target": geo, "Source Position": self.inp(pos, VECTOR)})
        dist = Val(FLOAT, o=n.out("Distance"), field=pos.field)
        if len(node.args) >= 3:
            hit = Val(VECTOR, o=n.out("Position"), field=pos.field)
            sn = self.g.add("GeometryNodeSampleNearest", {"domain": "FACE"},
                            {"Geometry": geo, "Sample Position": self.inp(hit, VECTOR)})
            self.assign_out(node.args[2], Val(INT, o=sn.out("Index"), field=pos.field), name)
        if len(node.args) == 4:
            self.note("xyzdist(): the uv output isn't available in Blender — it is set to {0, 0, 0}; "
                      'use primuv(geo, "attr", pos) to read attributes at the closest point')
            self.assign_out(node.args[3], Val(VECTOR, c=(0.0, 0.0, 0.0)), name)
        return dist

    def f_minpos(self, node, name):
        if len(node.args) != 2:
            raise FormulaError("minpos() takes minpos(geo, pos)")
        geo, _ = self.geo_arg(node.args[0], name)
        pos = self.coerce(self.expr(node.args[1]), VECTOR, "minpos()'s position")
        n = self.g.add("GeometryNodeProximity", {"target_element": "FACES"},
                       {"Target": geo, "Source Position": self.inp(pos, VECTOR)})
        return Val(VECTOR, o=n.out("Position"), field=pos.field)

    def f_primuv(self, node, name):
        if len(node.args) == 4:
            raise FormulaError('Blender samples surfaces by position: primuv(geo, "attr", pos) — the prim/uv '
                               'from xyzdist() aren\'t needed')
        if len(node.args) != 3:
            raise FormulaError('primuv() takes primuv(geo, "attr", pos)')
        geo, _ = self.geo_arg(node.args[0], name)
        spec = self._attr_spec(self.str_const(node.args[1], "primuv()'s attribute"), name)
        value = self._attr_field(spec)
        pos = self.coerce(self.expr(node.args[2]), VECTOR, "primuv()'s position")
        t, store = spec[2], spec[3]
        dtype = READ_DTYPE[store] if store in (COLOR, FLOAT2) else READ_DTYPE[t]
        n = self.g.add("GeometryNodeSampleNearestSurface", {"data_type": dtype},
                       {"Mesh": geo, "Value": self.inp(value, t), "Sample Position": self.inp(pos, VECTOR)})
        return Val(t, o=n.out("Value"), field=pos.field)

    def f_uvsample(self, node, name):
        if len(node.args) != 4:
            raise FormulaError('uvsample() takes uvsample(geo, "attr", "uvmap", uv)')
        geo, _ = self.geo_arg(node.args[0], name)
        spec = self._attr_spec(self.str_const(node.args[1], "uvsample()'s attribute"), name)
        value = self._attr_field(spec)
        uvname = self.str_const(node.args[2], "uvsample()'s uv map name")
        uvn = self.g.add("GeometryNodeInputNamedAttribute", {"data_type": "FLOAT_VECTOR"}, {"Name": uvname})
        uv = self.coerce(self.expr(node.args[3]), VECTOR, "uvsample()'s uv")
        t, store = spec[2], spec[3]
        dtype = READ_DTYPE[store] if store in (COLOR, FLOAT2) else READ_DTYPE[t]
        n = self.g.add("GeometryNodeSampleUVSurface", {"data_type": dtype},
                       {"Mesh": geo, "Value": self.inp(value, t), "Source UV Map": uvn.out("Attribute"),
                        "Sample UV": self.inp(uv, VECTOR)})
        return Val(t, o=n.out("Value"), field=uv.field)

    def _raycast(self, node, name, nargs):
        if len(node.args) not in nargs:
            raise FormulaError(f"{name}() takes {FUNCS[name].sig}")
        geo, _ = self.geo_arg(node.args[0], name)
        orig = self.coerce(self.expr(node.args[1]), VECTOR, f"{name}()'s origin")
        d = self.coerce(self.expr(node.args[2]), VECTOR, f"{name}()'s direction")
        n = self.g.add("GeometryNodeRaycast", {"data_type": "FLOAT"},
                       {"Target Geometry": geo, "Source Position": self.inp(orig, VECTOR),
                        "Ray Direction": self.inp(d, VECTOR),
                        "Ray Length": self.inp(self.vmath("LENGTH", d), FLOAT)})
        return n, geo, orig.field or d.field, orig, d

    def f_ray(self, node, name):
        n, geo, field, orig, d = self._raycast(node, name, {3})
        out = FUNCS[name].data["out"]
        if out == "Is Hit":
            return Val(BOOL, o=n.out(out), field=field)
        if out == "Hit Distance":
            return Val(FLOAT, o=n.out(out), field=field)
        v = Val(VECTOR, o=n.out(out), field=field)
        if out == "Hit Position":
            hit = Val(BOOL, o=n.out("Is Hit"), field=field)
            return self.switch(hit, self.vmath("ADD", orig, d), v, VECTOR)
        return v

    def f_intersect(self, node, name):
        n, geo, field, orig, d = self._raycast(node, name, {3, 4, 5})
        hit = Val(BOOL, o=n.out("Is Hit"), field=field)
        hitpos = Val(VECTOR, o=n.out("Hit Position"), field=field)
        sn = self.g.add("GeometryNodeSampleNearest", {"domain": "FACE"},
                        {"Geometry": geo, "Sample Position": hitpos.o})
        prim = self.switch(hit, Val(INT, c=-1), Val(INT, o=sn.out("Index"), field=field), INT)
        if len(node.args) >= 4:
            self.assign_out(node.args[3], self.switch(hit, self.vmath("ADD", orig, d), hitpos, VECTOR), name)
        if len(node.args) == 5:
            self.note("intersect(): the uvw output isn't available in Blender — it is set to {0, 0, 0}")
            self.assign_out(node.args[4], Val(VECTOR, c=(0.0, 0.0, 0.0)), name)
        return prim

    def _grid(self, geo, gname, t):
        n = self.g.add("GeometryNodeGetNamedGrid", {"data_type": SOCKET_DTYPE[t]},
                       {"Volume": geo, "Name": gname, "Remove": False})
        return n.out("Grid")

    def f_volumesample(self, node, name):
        if len(node.args) != 3:
            raise FormulaError(f'{name}() takes {name}(geo, "grid", pos)')
        t = FUNCS[name].data["t"]
        geo, _ = self.geo_arg(node.args[0], name)
        gname = self.str_const(node.args[1], f"{name}()'s grid name")
        pos = self.coerce(self.expr(node.args[2]), VECTOR, f"{name}()'s position")
        grid = self._grid(geo, gname, t)
        n = self.g.add("GeometryNodeSampleGrid", {"data_type": SOCKET_DTYPE[t]},
                       {"Grid": grid, "Position": self.inp(pos, VECTOR)})
        return Val(t, o=n.out("Value"), field=pos.field)

    def f_volumegradient(self, node, name):
        if len(node.args) != 3:
            raise FormulaError('volumegradient() takes volumegradient(geo, "grid", pos)')
        geo, _ = self.geo_arg(node.args[0], name)
        gname = self.str_const(node.args[1], "volumegradient()'s grid name")
        pos = self.coerce(self.expr(node.args[2]), VECTOR, "volumegradient()'s position")
        grad = self.g.add("GeometryNodeGridGradient", {}, {"Grid": self._grid(geo, gname, FLOAT)})
        n = self.g.add("GeometryNodeSampleGrid", {"data_type": "VECTOR"},
                       {"Grid": grad.out("Gradient"), "Position": self.inp(pos, VECTOR)})
        return Val(VECTOR, o=n.out("Value"), field=pos.field)

    def f_texture(self, node, name):
        if len(node.args) not in (2, 3):
            raise FormulaError(f"{name}() takes {name}(image, uv) or {name}(image, u, v)")
        img = self.expr(node.args[0])
        if img.t != IMAGE:
            raise FormulaError(f'{name}()\'s first argument is an image parameter: chimg("name")')
        if len(node.args) == 3:
            u = self.coerce(self.expr(node.args[1]), FLOAT, "u")
            v = self.coerce(self.expr(node.args[2]), FLOAT, "v")
            uv = self.combine([u, v, Val(FLOAT, c=0.0)])
        else:
            uv = self.coerce(self.expr(node.args[1]), VECTOR, f"{name}()'s uv")
        n = self.g.add("GeometryNodeImageTexture", {"interpolation": "Linear", "extension": "REPEAT"},
                       {"Image": img.o, "Vector": self.inp(uv, VECTOR)})
        return Val(VECTOR, o=n.out("Color"), field=uv.field)

    def f_spectrum(self, node, name):
        if len(node.args) not in (3, 4):
            raise FormulaError("spectrum() takes spectrum(sound, low_hz, high_hz [, time])")
        snd = self.expr(node.args[0])
        if snd.t != SOUND:
            raise FormulaError('spectrum()\'s first argument is a sound parameter: chsound("music")')
        low = self.coerce(self.expr(node.args[1]), FLOAT, "the low frequency")
        high = self.coerce(self.expr(node.args[2]), FLOAT, "the high frequency")
        if len(node.args) == 4:
            time = self.coerce(self.expr(node.args[3]), FLOAT, "the time")
        else:
            time = Val(FLOAT, o=self.g.add("GeometryNodeInputSceneTime").out("Seconds"))
        n = self.g.add("GeometryNodeSampleSoundFrequencies", {},
                       {"Sound": snd.o, "Time": self.inp(time, FLOAT), "Low": self.inp(low, FLOAT),
                        "High": self.inp(high, FLOAT)})
        return Val(FLOAT, o=n.out("Amplitude"), field=low.field or high.field or time.field)

    # ── topology ───────────────────────────────────────────────────────────
    def _topo_args(self, node, name, n):
        if len(node.args) != n:
            raise FormulaError(f"{name}() takes {FUNCS[name].sig}")
        _geo, is_self = self.geo_arg(node.args[0], name)
        if not is_self:
            raise FormulaError(f"{name}() works on input 0 (this geometry)")
        return [self.coerce(self.expr(a), INT, f"{name}()'s argument") for a in node.args[1:]]

    def _field_at(self, value, index, domain, t):
        if self._is_current_index(index, domain):
            return value
        n = self.g.add("GeometryNodeFieldAtIndex", {"domain": domain, "data_type": ATTR_DTYPE[t]},
                       {"Value": self.inp(value, t), "Index": self.inp(index, INT)})
        return Val(t, o=n.out("Value"), field=True)

    def f_neighbourcount(self, node, name):
        (pt,) = self._topo_args(node, name, 2)
        cnt = Val(INT, o=self.g.add("GeometryNodeInputMeshVertexNeighbors").out("Vertex Count"), field=True)
        return self._field_at(cnt, pt, "POINT", INT)

    def f_neighbour(self, node, name):
        pt, k = self._topo_args(node, name, 3)
        eov = self.g.add("GeometryNodeEdgesOfVertex", {}, {"Vertex Index": self.inp(pt, INT),
                                                           "Sort Index": self.inp(k, INT)})
        edge = Val(INT, o=eov.out("Edge Index"), field=True)
        total = Val(INT, o=eov.out("Total"), field=True)
        ev = self.g.add("GeometryNodeInputMeshEdgeVertices")
        v1 = self._field_at(Val(INT, o=ev.out("Vertex Index 1"), field=True), edge, "EDGE", INT)
        v2 = self._field_at(Val(INT, o=ev.out("Vertex Index 2"), field=True), edge, "EDGE", INT)
        other = self.switch(self.compare("EQUAL", v1, pt), v1, v2, INT)
        valid = self.boolean("AND", self.compare("LESS_THAN", k, total),
                             self.compare("GREATER_EQUAL", k, Val(INT, c=0)))
        return self.switch(valid, Val(INT, c=-1), other, INT)

    def f_primpoint(self, node, name):
        prim, k = self._topo_args(node, name, 3)
        cof = self.g.add("GeometryNodeCornersOfFace", {}, {"Face Index": self.inp(prim, INT),
                                                           "Sort Index": self.inp(k, INT)})
        voc = self.g.add("GeometryNodeVertexOfCorner", {}, {"Corner Index": cof.out("Corner Index")})
        pt = Val(INT, o=voc.out("Vertex Index"), field=True)
        valid = self.boolean("AND", self.compare("LESS_THAN", k, Val(INT, o=cof.out("Total"), field=True)),
                             self.compare("GREATER_EQUAL", k, Val(INT, c=0)))
        return self.switch(valid, Val(INT, c=-1), pt, INT)

    def f_primvertexcount(self, node, name):
        (prim,) = self._topo_args(node, name, 2)
        if self.domain == "CURVE":
            n = self.g.add("GeometryNodePointsOfCurve", {}, {"Curve Index": self.inp(prim, INT)})
        else:
            n = self.g.add("GeometryNodeCornersOfFace", {}, {"Face Index": self.inp(prim, INT)})
        return Val(INT, o=n.out("Total"), field=True)

    def f_vertexpoint(self, node, name):
        (vtx,) = self._topo_args(node, name, 2)
        n = self.g.add("GeometryNodeVertexOfCorner", {}, {"Corner Index": self.inp(vtx, INT)})
        return Val(INT, o=n.out("Vertex Index"), field=True)

    def f_vertexprim(self, node, name):
        (vtx,) = self._topo_args(node, name, 2)
        n = self.g.add("GeometryNodeFaceOfCorner", {}, {"Corner Index": self.inp(vtx, INT)})
        return Val(INT, o=n.out(FUNCS[name].data["out"]), field=True)

    def f_curvepoint(self, node, name):
        curve, k = self._topo_args(node, name, 3)
        n = self.g.add("GeometryNodePointsOfCurve", {}, {"Curve Index": self.inp(curve, INT),
                                                          "Sort Index": self.inp(k, INT)})
        return Val(INT, o=n.out("Point Index"), field=True)

    def f_pointcurve(self, node, name):
        (pt,) = self._topo_args(node, name, 2)
        n = self.g.add("GeometryNodeCurveOfPoint", {}, {"Point Index": self.inp(pt, INT)})
        return Val(INT, o=n.out("Curve Index"), field=True)

    # ── aggregates ─────────────────────────────────────────────────────────
    _STAT_OUT = {"sum": "Sum", "mean": "Mean", "min": "Min", "max": "Max", "median": "Median",
                 "stdev": "Standard Deviation", "variance": "Variance", "count": "Sum"}

    def f_aggregate(self, node, name):
        op = FUNCS[name].data["op"]
        vals = self.args(node, name, {1, 2})
        x = vals[0]
        if op == "count":
            x = self.coerce(self.as_bool(x, "countof()'s condition"), FLOAT)
        if x.t not in (FLOAT, INT, BOOL, VECTOR):
            raise FormulaError(f"{name}() works on numbers and vectors, not {type_word(x.t)}")
        vec = x.t == VECTOR
        xin = x if vec else self.coerce(x, FLOAT, f"{name}()'s value")
        dom = self.field_domain()
        if len(vals) == 1:
            n = self.g.add("GeometryNodeAttributeStatistic",
                           {"data_type": "FLOAT_VECTOR" if vec else "FLOAT", "domain": dom},
                           {"Geometry": self.ctx.geo, "Attribute": self.inp(xin, VECTOR if vec else FLOAT)})
            t = VECTOR if vec else (INT if op == "count" else FLOAT)
            return Val(t, o=n.out(self._STAT_OUT[op]), field=False)
        self.require("field_stats")
        group = self.coerce(vals[1], INT, f"{name}()'s group")
        dtype = "FLOAT_VECTOR" if vec else "FLOAT"
        t = VECTOR if vec else FLOAT
        inputs = {"Value": self.inp(xin, t), "Group Index": self.inp(group, INT)}
        if op in ("mean", "median"):
            n = self.g.add("GeometryNodeFieldAverage", {"data_type": dtype, "domain": dom}, inputs)
            out = "Mean" if op == "mean" else "Median"
        elif op in ("min", "max"):
            n = self.g.add("GeometryNodeFieldMinAndMax", {"data_type": dtype, "domain": dom}, inputs)
            out = "Min" if op == "min" else "Max"
        elif op in ("stdev", "variance"):
            n = self.g.add("GeometryNodeFieldVariance", {"data_type": dtype, "domain": dom}, inputs)
            out = "Standard Deviation" if op == "stdev" else "Variance"
        else:   # sum / count
            n = self.g.add("GeometryNodeAccumulateField", {"data_type": dtype, "domain": dom}, inputs)
            out = "Total"
        return Val(INT if op == "count" else t, o=n.out(out), field=True)

    def f_accumulate(self, node, name):
        vals = self.args(node, name, {1, 2})
        x = vals[0]
        if x.t not in (FLOAT, INT, BOOL, VECTOR):
            raise FormulaError("accumulate() works on numbers and vectors")
        t = VECTOR if x.t == VECTOR else (INT if x.t in (INT, BOOL) else FLOAT)
        dtype = {VECTOR: "FLOAT_VECTOR", INT: "INT", FLOAT: "FLOAT"}[t]
        inputs = {"Value": self.inp(x, t)}
        if len(vals) == 2:
            inputs["Group Index"] = self.inp(vals[1], INT, "accumulate()'s group")
        n = self.g.add("GeometryNodeAccumulateField", {"data_type": dtype, "domain": self.field_domain()}, inputs)
        return Val(t, o=n.out("Leading"), field=True)

    def f_blur(self, node, name):
        vals = self.args(node, name, {2, 3})
        x = vals[0]
        if x.t not in (FLOAT, INT, VECTOR):
            raise FormulaError("blur() works on floats, ints and vectors")
        dtype = {FLOAT: "FLOAT", INT: "INT", VECTOR: "FLOAT_VECTOR"}[x.t]
        inputs = {"Value": self.inp(x, x.t), "Iterations": self.inp(vals[1], INT, "blur()'s iterations")}
        if len(vals) == 3:
            inputs["Weight"] = self.inp(vals[2], FLOAT, "blur()'s weight")
        n = self.g.add("GeometryNodeBlurAttribute", {"data_type": dtype}, inputs)
        return Val(x.t, o=n.out("Value"), field=True)

    def f_atindex(self, node, name):
        x, idx = self.args(node, name, {2})
        if x.t not in ATTR_DTYPE or x.t == STRING:
            raise FormulaError(f"atindex() can't evaluate {type_word(x.t)}")
        idx = self.coerce(idx, INT, "atindex()'s index")
        n = self.g.add("GeometryNodeFieldAtIndex", {"domain": self.field_domain(), "data_type": ATTR_DTYPE[x.t]},
                       {"Value": self.inp(x, x.t), "Index": self.inp(idx, INT)})
        return Val(x.t, o=n.out("Value"), field=True)

    def f_ondomain(self, node, name):
        if len(node.args) != 2:
            raise FormulaError('ondomain() takes ondomain(value, "prim")')
        x = self.expr(node.args[0])
        word = self.str_const(node.args[1], "ondomain()'s domain").strip().lower()
        if word not in RUNOVER_DOMAINS or RUNOVER_DOMAINS[word] == "DETAIL":
            raise FormulaError(f"ondomain(): unknown domain '{word}' — point, prim, edge, vertex, curve or instance")
        if x.t not in ATTR_DTYPE or x.t == STRING:
            raise FormulaError(f"ondomain() can't move {type_word(x.t)}")
        n = self.g.add("GeometryNodeFieldOnDomain", {"domain": RUNOVER_DOMAINS[word], "data_type": ATTR_DTYPE[x.t]},
                       {"Value": self.inp(x, x.t)})
        return Val(x.t, o=n.out("Value"), field=True)

    # ── detail attributes (stored in the geometry's bundle) ────────────────
    def bundle_item(self, geo, name, t):
        self.require("geometry_bundles")
        gb = self.g.add("GeometryNodeGetGeometryBundle", {}, {"Geometry": geo})
        gi = self.g.add("NodeGetBundleItem", {"socket_type": SOCKET_DTYPE[t]},
                        {"Bundle": gb.out("Bundle"), "Path": name})
        return Val(t, o=gi.out("Item"), field=False), Val(BOOL, o=gi.out("Exists"), field=False)

    def read_detail(self, name, t):
        return self.bundle_item(self.ctx.geo, name, t)[0]

    def write_detail(self, name, t, value):
        self.require("geometry_bundles")
        if t in (STRING,) and value.field:
            self.require("string_fields")
        if value.field:
            raise FormulaError(f"detail attribute '{name}' holds one value for the whole geometry, but this "
                               f"value differs per element — use an aggregate like avgof(), sumof(), maxof() "
                               f"or setdetailattrib(0, \"{name}\", value, \"add\")")
        sel = self.selection()
        if sel is not None:
            if sel.field:
                raise FormulaError("a detail attribute can't change under a per-element condition — use "
                                   "setdetailattrib(..., \"add\"/\"max\") to combine elements")
            value = self.switch(sel, self.read_detail(name, t), value, t)
        gb = self.g.add("GeometryNodeGetGeometryBundle", {}, {"Geometry": self.ctx.geo})
        sbi = self.g.add("NodeStoreBundleItem", {"socket_type": SOCKET_DTYPE[t]},
                         {"Bundle": gb.out("Bundle"), "Path": name, "Item": self.inp(value, t)}, pure=False)
        sgb = self.g.add("GeometryNodeSetGeometryBundle", {}, {"Geometry": gb.out("Geometry"),
                                                               "Bundle": sbi.out("Bundle")},
                         pure=False, label=(self.cur_text[:60] or None))
        self.ctx.geo = sgb.out("Geometry")
        self.detail_attrs[name] = t
        self.writes += 1

    def f_setdetailattrib(self, node, name):
        if len(node.args) not in (3, 4):
            raise FormulaError('setdetailattrib() takes setdetailattrib(0, "name", value [, mode])')
        _geo, is_self = self.geo_arg(node.args[0], name)
        if not is_self:
            raise FormulaError("setdetailattrib() writes to input 0")
        text = self.str_const(node.args[1], "the attribute name").strip()
        from .core import PREFIX_TYPE
        prefix, _, raw = text.rpartition("@")
        value = self.expr(node.args[2])
        mode = self.str_const(node.args[3], "the mode").strip().lower() if len(node.args) == 4 else "set"
        if mode not in ("set", "add", "min", "max", "mean", "average"):
            raise FormulaError(f"setdetailattrib(): unknown mode '{mode}' — set, add, min, max or mean")
        t = PREFIX_TYPE[prefix] if prefix else (self.detail_attrs.get(raw) or
                                                (VECTOR if value.t == VECTOR else
                                                 (value.t if value.t in (INT, STRING, ROTATION, MATRIX) else FLOAT)))
        sel_now = self.selection()
        if value.field or (sel_now is not None and sel_now.field):
            if mode == "set":
                raise FormulaError("setdetailattrib(): every element would set its own value — use mode "
                                   "\"add\", \"min\", \"max\" or \"mean\" to combine them")
            if t not in (FLOAT, INT, VECTOR):
                raise FormulaError("setdetailattrib(): only numbers and vectors can be combined")
            vec = t == VECTOR
            xin = self.coerce(value, VECTOR if vec else FLOAT, "the value")
            inputs = {"Geometry": self.ctx.geo, "Attribute": self.inp(xin, VECTOR if vec else FLOAT)}
            sel = self.selection()
            if sel is not None:
                inputs["Selection"] = self.inp(sel, BOOL)
            st = self.g.add("GeometryNodeAttributeStatistic",
                            {"data_type": "FLOAT_VECTOR" if vec else "FLOAT", "domain": self.field_domain()}, inputs)
            out = {"add": "Sum", "min": "Min", "max": "Max", "mean": "Mean", "average": "Mean"}[mode]
            combined = Val(VECTOR if vec else FLOAT, o=st.out(out), field=False)
            old = self.read_detail(raw, t) if raw in self.detail_attrs else None
            if old is not None and mode == "add":
                combined = self.binop(ast.Add, old, combined)
            elif old is not None and mode in ("min", "max"):
                combined = (self.vmath if vec else self.math)("MINIMUM" if mode == "min" else "MAXIMUM",
                                                              old, combined)
            # the selection already went into the statistic: write unconditionally
            self._write_detail_unconditional(raw, t, self.coerce(combined, t))
            return Val(VOID)
        value = self.coerce(value, t, "the value")
        if mode in ("add", "min", "max") and raw in self.detail_attrs:
            old = self.read_detail(raw, t)
            if mode == "add":
                value = self.binop(ast.Add, old, value)
            else:
                value = (self.vmath if t == VECTOR else self.math)("MINIMUM" if mode == "min" else "MAXIMUM",
                                                                   old, value)
        self.write_detail(raw, t, value)
        return Val(VOID)

    def _write_detail_unconditional(self, name, t, value):
        gb = self.g.add("GeometryNodeGetGeometryBundle", {}, {"Geometry": self.ctx.geo})
        sbi = self.g.add("NodeStoreBundleItem", {"socket_type": SOCKET_DTYPE[t]},
                         {"Bundle": gb.out("Bundle"), "Path": name, "Item": self.inp(value, t)}, pure=False)
        sgb = self.g.add("GeometryNodeSetGeometryBundle", {}, {"Geometry": gb.out("Geometry"),
                                                               "Bundle": sbi.out("Bundle")},
                         pure=False, label=(self.cur_text[:60] or None))
        self.ctx.geo = sgb.out("Geometry")
        self.detail_attrs[name] = t
        self.writes += 1

    def f_setelemattrib(self, node, name):
        domain = FUNCS[name].data["domain"]
        if len(node.args) not in (4, 5):
            raise FormulaError(f'{name}() takes {name}(0, "name", index, value [, mode])')
        _geo, is_self = self.geo_arg(node.args[0], name)
        if not is_self:
            raise FormulaError(f"{name}() writes to input 0")
        text = self.str_const(node.args[1], "the attribute name").strip()
        prefix, _, raw = text.rpartition("@")
        idx = self.coerce(self.expr(node.args[2]), INT, f"{name}()'s index")
        value = self.expr(node.args[3])
        mode = self.str_const(node.args[4], "the mode").strip().lower() if len(node.args) == 5 else "set"
        if mode not in ("set", "add"):
            raise FormulaError(f"{name}(): mode must be \"set\" or \"add\"")
        here = self._is_current_index(idx, domain)
        if not here and (idx.field or value.field):
            raise FormulaError(f"{name}() can write to this element, or to one fixed element with a single value "
                               f"— to change many elements, write the attribute directly")
        attr = self.resolve_attr(prefix if prefix else ("f" if value.t == FLOAT else
                                                        {INT: "i", BOOL: "b", VECTOR: "v"}.get(value.t, "")),
                                 raw, write=True)
        value = self.coerce(value, attr.t, f"{name}()'s value")
        if mode == "add":
            value = self.binop(ast.Add, self.read_attr(attr), value)
        saved = self.scope
        if not here:
            cond = self.compare("EQUAL", Val(INT, o=self.g.add("GeometryNodeInputIndex").out("Index"), field=True),
                                idx)
            cb = self._cond_binding(cond)
            self._push("if", cond=cb, domain=domain)
        else:
            self._push("block", domain=domain)
        try:
            self._emit_write(attr, value=value)
        finally:
            self.scope = saved
        return Val(VOID)

    def _cond_binding(self, cond):
        from .compiler import Binding
        return Binding(f"if#c{self._new_uid()}", BOOL, cond, self.ctx, self.cur_line, self.field_domain(), self.epoch)

    # ── instances ──────────────────────────────────────────────────────────
    def read_instance_xform(self, a):
        name = a.name
        if name in ("orient", "rotation"):
            return Val(ROTATION, o=self.g.add("GeometryNodeInputInstanceRotation").out("Rotation"), field=True)
        if name == "scale":
            return Val(VECTOR, o=self.g.add("GeometryNodeInputInstanceScale").out("Scale"), field=True)
        if name == "radius":
            return self.sep(Val(VECTOR, o=self.g.add("GeometryNodeInputInstanceScale").out("Scale"), field=True), 0)
        if name == "transform":
            return Val(MATRIX, o=self.g.add("GeometryNodeInstanceTransform").out("Transform"), field=True)
        raise FormulaError(f"unknown instance attribute '{name}'")

    def write_instance_transform(self, attr, value, offset, sel, label):
        name = attr.name
        if name == "transform":
            m = self.coerce(value, MATRIX)
        else:
            pos = Val(VECTOR, o=self.g.add("GeometryNodeInputPosition").out("Position"), field=True)
            rot = Val(ROTATION, o=self.g.add("GeometryNodeInputInstanceRotation").out("Rotation"), field=True)
            scale = Val(VECTOR, o=self.g.add("GeometryNodeInputInstanceScale").out("Scale"), field=True)
            if name in ("orient", "rotation"):
                rot = self.coerce(value, ROTATION)
            elif name == "scale":
                scale = self.coerce(value, VECTOR)
            elif name == "radius":
                scale = self.coerce(self.coerce(value, FLOAT), VECTOR)
            ct = self.g.add("FunctionNodeCombineTransform", {},
                            {"Translation": self.inp(pos, VECTOR), "Rotation": self.inp(rot, ROTATION),
                             "Scale": self.inp(scale, VECTOR)})
            m = Val(MATRIX, o=ct.out("Transform"), field=True)
        inputs = {"Instances": self.ctx.geo, "Transform": self.inp(m, MATRIX)}
        if sel is not None:
            inputs["Selection"] = self.inp(sel, BOOL)
        node = self.g.add("GeometryNodeSetInstanceTransform", {}, inputs, pure=False, label=label)
        self.ctx.geo = node.out("Instances")
