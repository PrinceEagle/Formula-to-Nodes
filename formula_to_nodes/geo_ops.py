# SPDX-License-Identifier: GPL-3.0-or-later
"""Geometry operations (statements that change the geometry itself) and the
deferred effects removepoint / removeprim / addpoint.

Like Houdini wrangles, removals and additions take effect at the end of the
script (or of the zone they're in). Operations such as scatter() replace the
elements, so per-element variables computed before them can't be read after.
"""

import ast

from .core import (FLOAT, INT, BOOL, VECTOR, ROTATION, OBJECT, COLLECTION, MATERIAL, VOID, Val, FUNCS, reg,
                   FormulaError, ITEM_TYPE)

# deferred effects
reg("removepoint", "removepoint(geo, ptnum)", "deletes this point (or one fixed point) when the script ends",
    "Effects", "f_remove", effect=True, what="point")
reg("removeprim", "removeprim(geo, primnum [, andpoints])",
    "deletes this face/curve (or one fixed one) when the script ends; andpoints=0 keeps its points",
    "Effects", "f_remove", effect=True, what="prim")
reg("addpoint", "addpoint(geo, pos)", "adds a point at pos for every element that runs it (inside an if: only those)",
    "Effects", "f_addpoint", effect=True, expr_ok=True)

# geometry operations
reg("scatter", "scatter(density [, seed, mindist])",
    "points on the faces (density per m², a per-face value works); later lines run on the points, "
    "which get @N and p@orient", "Geometry operations", "g_scatter", effect=True)
reg("instance", "instance(source [, index])",
    'copies source onto every point — chobj("x"), chcoll("x") (index picks a child) or 1 (Input 1); '
    "uses p@orient, v@scale and @pscale like Houdini's Copy to Points; later lines run over the instances",
    "Geometry operations", "g_instance", effect=True)
reg("realize", "realize()", "turns instances into real geometry (back to running over points)",
    "Geometry operations", "g_realize", effect=True)
reg("subdivide", "subdivide(levels)", "simple subdivision (keeps the shape)", "Geometry operations", "g_simple",
    effect=True, node="GeometryNodeSubdivideMesh", gin="Mesh", gout="Mesh", args=(("Level", INT, 1),))
reg("subdivsurf smoothsubdiv", "NAME(levels)", "Catmull-Clark subdivision (smooths)", "Geometry operations",
    "g_simple", effect=True, node="GeometryNodeSubdivisionSurface", gin="Mesh", gout="Mesh",
    args=(("Level", INT, 1),))
reg("triangulate", "triangulate()", "splits faces into triangles (inside an if: only those faces)",
    "Geometry operations", "g_simple", effect=True, node="GeometryNodeTriangulate", gin="Mesh", gout="Mesh",
    sel="Selection")
reg("dualmesh", "dualmesh()", "dual mesh (faces ↔ points: hexagon patterns)", "Geometry operations", "g_simple",
    effect=True, node="GeometryNodeDualMesh", gin="Mesh", gout="Dual Mesh")
reg("convexhull", "convexhull()", "convex hull of the geometry", "Geometry operations", "g_simple", effect=True,
    node="GeometryNodeConvexHull", gin="Geometry", gout="Convex Hull")
reg("fuse mergebydistance", "NAME(distance)", "merges points closer than distance", "Geometry operations",
    "g_simple", effect=True, node="GeometryNodeMergeByDistance", gin="Geometry", gout="Geometry",
    sel="Selection", args=(("Distance", FLOAT, 0.001),))
reg("extrude", "extrude(offset [, individual])",
    "extrudes faces along their normals (inside an if: only those); marks b@extrudeFront and b@extrudeSide",
    "Geometry operations", "g_extrude", effect=True)
reg("bevel", "bevel(width [, segments])", "bevels edges (inside an if: only those edges)",
    "Geometry operations", "g_bevel", effect=True, feature="mesh_bevel")
reg("resample", "resample(count)", "resamples curves to count points each", "Geometry operations", "g_simple",
    effect=True, node="GeometryNodeResampleCurve", gin="Curve", gout="Curve", sel="Selection",
    args=(("Count", INT, 10),), fixed={"Mode": "Count"})
reg("sweep", "sweep(radius [, resolution])", "turns curves into tubes (radius may vary per point)",
    "Geometry operations", "g_sweep", effect=True)
reg("tocurves", "tocurves()", "mesh edges → curves", "Geometry operations", "g_simple", effect=True,
    node="GeometryNodeMeshToCurve", gin="Mesh", gout="Curve", sel="Selection", props={"mode": "EDGES"})
reg("topoints", "topoints()", "mesh → a point cloud (one point per vertex, keeps @N)", "Geometry operations",
    "g_topoints", effect=True)
reg("points", "points(count)", "replaces the geometry with count points at the origin (set @P next)",
    "Geometry operations", "g_points", effect=True)
reg("grid", "grid(size_x, size_y, verts_x, verts_y)", "replaces the geometry with a grid mesh",
    "Geometry operations", "g_grid", effect=True)
reg("join", "join(geo)", 'adds other geometry: 1 (Input 1) or chobj("name")', "Geometry operations", "g_join",
    effect=True)
reg("transform", "transform(translate [, rotate, scale])",
    "moves/rotates/scales the whole geometry (rotate: rotation or Euler radians)", "Geometry operations",
    "g_transform", effect=True)
reg("setmaterial", 'setmaterial(chmat("name"))', "assigns a material (inside an if: only those faces)",
    "Geometry operations", "g_setmaterial", effect=True)
reg("shadesmooth", "shadesmooth(smooth)", "smooth (1) or flat (0) shading (inside an if: only those faces)",
    "Geometry operations", "g_shadesmooth", effect=True)
reg("flipfaces", "flipfaces()", "flips face normals (inside an if: only those faces)", "Geometry operations",
    "g_simple", effect=True, node="GeometryNodeFlipFaces", gin="Mesh", gout="Mesh", sel="Selection",
    keep_elements=True)
reg("sortpoints", "sortpoints(weight [, group])", "reorders points by weight (smallest first)",
    "Geometry operations", "g_sortpoints", effect=True)


class GeoOps:
    # ── deferred effects ───────────────────────────────────────────────────
    def _flush_effects(self, ctx):
        """Apply pending removals, then join pending additions."""
        if ctx.removals:
            for dom, (flag, mode) in list(ctx.removals.items()):
                d = self.g.add("GeometryNodeDeleteGeometry", {"domain": dom, "mode": mode},
                               {"Geometry": ctx.geo, "Selection": flag.o if not flag.is_const else bool(flag.c)},
                               pure=False, label=f"removed {dom.lower()}s")
                ctx.geo = d.out("Geometry")
            ctx.removals = {}
        if ctx.additions:
            j = self.g.add("GeometryNodeJoinGeometry", {}, {"Geometry": [ctx.geo] + ctx.additions}, pure=False,
                           label="added points")
            ctx.geo = j.out("Geometry")
            ctx.additions = []

    def f_remove(self, node, name):
        what = FUNCS[name].data["what"]
        nargs = {2} if what == "point" else {2, 3}
        if len(node.args) not in nargs:
            raise FormulaError(f"{name}() takes {FUNCS[name].sig}")
        _geo, is_self = self.geo_arg(node.args[0], name)
        if not is_self:
            raise FormulaError(f"{name}() removes from input 0")
        idx = self.coerce(self.expr(node.args[1]), INT, f"{name}()'s index")
        dom = self.field_domain()
        if what == "point":
            rdom = "INSTANCE" if dom == "INSTANCE" else "POINT"
            mode = "ALL"
        else:
            rdom = "CURVE" if dom == "CURVE" else "FACE"
            mode = "ALL"
            if len(node.args) == 3:
                ap = self.coerce(self.expr(node.args[2]), BOOL, "andpoints")
                if not ap.is_const:
                    raise FormulaError("removeprim()'s andpoints must be 0 or 1")
                mode = "ALL" if ap.c else "ONLY_FACE"
                if rdom == "CURVE":
                    mode = "ALL"
        flag = self.selection()
        if not self._is_current_index(idx, rdom):
            if idx.field:
                raise FormulaError(f"{name}() can remove this element (@ptnum / @primnum) or one fixed index — "
                                   f"to remove several, call it inside if (...)")
            here = self.compare("EQUAL", Val(INT, o=self.g.add("GeometryNodeInputIndex").out("Index"), field=True),
                                idx)
            flag = here if flag is None else self.boolean("AND", flag, here)
        if flag is None:
            flag = Val(BOOL, c=True)
        prev = self.ctx.removals.get(rdom)
        if prev is not None:
            flag = self.boolean("OR", prev[0], flag)
            if prev[1] != mode:
                self.note(f"{name}(): mixed andpoints values — the last one wins")
        cap = self.g.add("GeometryNodeCaptureAttribute", {"domain": rdom},
                         {"Geometry": self.ctx.geo, "item:0": self.inp(flag, BOOL)}, pure=False,
                         items=[("BOOLEAN", f"remove {rdom.lower()}")], items_attr="capture_items",
                         label=f"to remove ({self.cur_text[:40]})")
        self.ctx.geo = cap.out("Geometry")
        self.ctx.removals[rdom] = (Val(BOOL, o=cap.out("item:0"), field=True), mode)
        return Val(VOID)

    def f_addpoint(self, node, name):
        if len(node.args) != 2:
            raise FormulaError("addpoint() takes addpoint(geo, pos)")
        _geo, is_self = self.geo_arg(node.args[0], name)
        if not is_self:
            raise FormulaError("addpoint() adds to input 0")
        pos = self.coerce(self.expr(node.args[1]), VECTOR, "addpoint()'s position")
        dom = self.domain
        sel = self.selection()
        if dom == "DETAIL":
            count = Val(INT, c=1) if sel is None else self.switch(sel, Val(INT, c=0), Val(INT, c=1), INT)
            p = self.g.add("GeometryNodePoints", {}, {"Count": self.inp(count, INT), "Position": self.inp(pos, VECTOR)},
                           pure=False, label=self.cur_text[:60] or None)
            self.ctx.additions.append(p.out("Geometry"))
        elif dom in ("POINT", "INSTANCE"):
            cap = self.g.add("GeometryNodeCaptureAttribute", {"domain": dom},
                             {"Geometry": self.ctx.geo, "item:0": self.inp(pos, VECTOR)}, pure=False,
                             items=[("VECTOR", "new position")], items_attr="capture_items")
            self.ctx.geo = cap.out("Geometry")
            if dom == "INSTANCE":
                inputs = {"Instances": cap.out("Geometry"), "Position": cap.out("item:0")}
                if sel is not None:
                    inputs["Selection"] = self.inp(sel, BOOL)
                p = self.g.add("GeometryNodeInstancesToPoints", {}, inputs, pure=False)
                self.ctx.additions.append(p.out("Points"))
            else:
                inputs = {"Geometry": cap.out("Geometry"), "Amount": 1}
                if sel is not None:
                    inputs["Selection"] = self.inp(sel, BOOL)
                dup = self.g.add("GeometryNodeDuplicateElements", {"domain": "POINT"}, inputs, pure=False,
                                 label=self.cur_text[:60] or None)
                sp = self.g.add("GeometryNodeSetPosition", {}, {"Geometry": dup.out("Geometry"),
                                                                "Position": cap.out("item:0")}, pure=False)
                self.ctx.additions.append(sp.out("Geometry"))
        elif dom in ("FACE", "EDGE", "CORNER"):
            mode = {"FACE": "FACES", "EDGE": "EDGES", "CORNER": "CORNERS"}[dom]
            inputs = {"Mesh": self.ctx.geo, "Position": self.inp(pos, VECTOR)}
            if sel is not None:
                inputs["Selection"] = self.inp(sel, BOOL)
            m2p = self.g.add("GeometryNodeMeshToPoints", {"mode": mode}, inputs, pure=False,
                             label=self.cur_text[:60] or None)
            p2v = self.g.add("GeometryNodePointsToVertices", {}, {"Points": m2p.out("Points")}, pure=False)
            self.ctx.additions.append(p2v.out("Mesh"))
        else:
            raise FormulaError(f"addpoint() works per point, face, edge, vertex, instance or in detail mode")
        if not self._call_is_stmt:
            self.note("addpoint(): the new point's number isn't known while the script runs — it returns -1")
        return Val(INT, c=-1)

    # ── helpers ────────────────────────────────────────────────────────────
    def _geo_op(self, fname, idname, props, inputs, gin, gout, sel_key=None, reason=None, keep_elements=False):
        self._flush_effects(self.ctx)
        if keep_elements or reason is None:
            self.capture_live(after=self.cur_sid)
        sel = self.selection()
        if sel is not None:
            if sel_key is None:
                raise FormulaError(f"{fname}() changes the whole geometry, so it can't be inside an if — "
                                   f"use a fixed condition or move it out")
            inputs[sel_key] = self.inp(sel, BOOL)
        inputs[gin] = self.ctx.geo
        node = self.g.add(idname, props, inputs, pure=False, label=self.cur_text[:60] or None)
        self.ctx.geo = node.out(gout)
        if reason and not keep_elements:
            self._new_epoch(reason)
        self.writes += 1
        return node

    def _single(self, v, what):
        if v.field:
            raise FormulaError(f"{what} must be the same for every element")
        return v

    def g_simple(self, node, name):
        d = FUNCS[name].data
        spec = d.get("args", ())
        if len(node.args) > len(spec):
            raise FormulaError(f"{name}() takes {FUNCS[name].sig}")
        inputs = dict(d.get("fixed", {}))
        for (sock, t, default), a in zip(spec, node.args):
            inputs[sock] = self.inp(self.expr(a), t, f"{name}()'s {sock.lower()}")
        self._geo_op(name, d["node"], dict(d.get("props", {})), inputs, d["gin"], d["gout"], d.get("sel"),
                     reason=f"{name}()", keep_elements=d.get("keep_elements", False))
        if d["node"] in ("GeometryNodeMeshToCurve",):
            self.point_normals = False
        return Val(VOID)

    # ── scatter / instance ─────────────────────────────────────────────────
    def g_scatter(self, node, name):
        vals = self.args(node, name, {1, 2, 3})
        density = self.coerce(vals[0], FLOAT, "scatter()'s density")
        seed = self._single(self.coerce(vals[1], INT, "scatter()'s seed"), "the seed") if len(vals) > 1 \
            else Val(INT, c=0)
        inputs = {"Seed": self.inp(seed, INT)}
        if len(vals) == 3:
            mind = self._single(self.coerce(vals[2], FLOAT, "scatter()'s min distance"), "the min distance")
            props = {"distribute_method": "POISSON"}
            inputs["Distance Min"] = self.inp(mind, FLOAT)
            inputs["Density Max"] = self.inp(self._single(density, "the density with a min distance"), FLOAT)
        else:
            props = {"distribute_method": "RANDOM"}
            inputs["Density"] = self.inp(density, FLOAT)
        dist = self._geo_op(name, "GeometryNodeDistributePointsOnFaces", props, inputs, "Mesh", "Points",
                            sel_key="Selection", reason="scatter()")
        st = self.g.add("GeometryNodeStoreNamedAttribute", {"data_type": "FLOAT_VECTOR", "domain": "POINT"},
                        {"Geometry": dist.out("Points"), "Name": "N", "Value": dist.out("Normal")}, pure=False)
        st2 = self.g.add("GeometryNodeStoreNamedAttribute", {"data_type": "QUATERNION", "domain": "POINT"},
                         {"Geometry": st.out("Geometry"), "Name": "orient", "Value": dist.out("Rotation")},
                         pure=False)
        self.ctx.geo = st2.out("Geometry")
        self.attr_info["N"] = (VECTOR, None, "POINT")
        self.attr_info["orient"] = (ROTATION, None, "POINT")
        self.point_normals = True
        return Val(VOID)

    def g_instance(self, node, name):
        if len(node.args) not in (1, 2):
            raise FormulaError('instance() takes instance(chobj("name") or chcoll("name") or 1 [, index])')
        src_node = node.args[0]
        pick = False
        if isinstance(src_node, ast.Constant) and isinstance(src_node.value, (int, float)) \
                and not isinstance(src_node.value, bool):
            n = int(src_node.value)
            if n < 1:
                raise FormulaError("instance(0) would copy the points onto themselves — use 1 (the Input 1 "
                                   "object), chobj() or chcoll()")
            src = self.opinput_geo(n)
        else:
            v = self.expr(src_node)
            if v.t == OBJECT:
                oi = self.g.add("GeometryNodeObjectInfo", {"transform_space": "ORIGINAL"},
                                {"Object": v.o, "As Instance": True})
                src = oi.out("Geometry")
            elif v.t == COLLECTION:
                ci = self.g.add("GeometryNodeCollectionInfo", {"transform_space": "ORIGINAL"},
                                {"Collection": v.o, "Separate Children": True, "Reset Children": True})
                src = ci.out("Instances")
                pick = True
            else:
                raise FormulaError('instance() takes chobj("name"), chcoll("name") or an input number')
        inputs = {"Instance": src}
        if pick:
            inputs["Pick Instance"] = True
            if len(node.args) == 2:
                inputs["Instance Index"] = self.inp(self.expr(node.args[1]), INT, "instance()'s index")
        elif len(node.args) == 2:
            raise FormulaError("instance()'s index picks a child of a collection — use chcoll(\"name\")")
        orient = self.g.add("GeometryNodeInputNamedAttribute", {"data_type": "QUATERNION"}, {"Name": "orient"})
        rot = self.switch(Val(BOOL, o=orient.out("Exists"), field=True), Val(ROTATION, c=(0.0, 0.0, 0.0)),
                          Val(ROTATION, o=orient.out("Attribute"), field=True), ROTATION)
        sc = self.g.add("GeometryNodeInputNamedAttribute", {"data_type": "FLOAT_VECTOR"}, {"Name": "scale"})
        scale = self.switch(Val(BOOL, o=sc.out("Exists"), field=True), Val(VECTOR, c=(1.0, 1.0, 1.0)),
                            Val(VECTOR, o=sc.out("Attribute"), field=True), VECTOR)
        if getattr(self, "wrote_pscale", False):
            rad = Val(FLOAT, o=self.g.add("GeometryNodeInputRadius").out("Radius"), field=True)
            scale = self.vmath("SCALE", scale, scale=rad)
        inputs["Rotation"] = self.inp(rot, ROTATION)
        inputs["Scale"] = self.inp(scale, VECTOR)
        self._geo_op(name, "GeometryNodeInstanceOnPoints", {}, inputs, "Points", "Instances",
                     sel_key="Selection", reason="instance()")
        self.scope.domain = "INSTANCE"
        self.point_normals = False
        return Val(VOID)

    def g_realize(self, node, name):
        self.args(node, name, {0})
        self._geo_op(name, "GeometryNodeRealizeInstances", {}, {}, "Geometry", "Geometry", sel_key="Selection",
                     reason="realize()")
        if self.domain == "INSTANCE":
            self.scope.domain = "POINT"
        return Val(VOID)

    def g_extrude(self, node, name):
        vals = self.args(node, name, {1, 2})
        inputs = {"Offset Scale": self.inp(vals[0], FLOAT, "extrude()'s offset")}
        if len(vals) == 2:
            inputs["Individual"] = self.inp(self._single(self.coerce(vals[1], BOOL, "individual"), "individual"), BOOL)
        ex = self._geo_op(name, "GeometryNodeExtrudeMesh", {"mode": "FACES"}, inputs, "Mesh", "Mesh",
                          sel_key="Selection", reason="extrude()")
        geo = ex.out("Mesh")
        for attr, sock in (("extrudeFront", "Top"), ("extrudeSide", "Side")):
            st = self.g.add("GeometryNodeStoreNamedAttribute", {"data_type": "BOOLEAN", "domain": "FACE"},
                            {"Geometry": geo, "Name": attr, "Value": ex.out(sock)}, pure=False)
            geo = st.out("Geometry")
            self.attr_info[attr] = (BOOL, None, "FACE")
        self.ctx.geo = geo
        return Val(VOID)

    def g_bevel(self, node, name):
        vals = self.args(node, name, {1, 2})
        inputs = {"Offset": self.inp(vals[0], FLOAT, "bevel()'s width"), "Affect Kind": "Edges"}
        if len(vals) == 2:
            inputs["Segments"] = self.inp(vals[1], INT, "bevel()'s segments")
        self._geo_op(name, "GeometryNodeMeshBevel", {}, inputs, "Mesh", "Mesh", sel_key="Selection",
                     reason="bevel()")
        return Val(VOID)

    def g_sweep(self, node, name):
        vals = self.args(node, name, {1, 2})
        res = self._single(self.coerce(vals[1], INT, "sweep()'s resolution"), "the resolution") if len(vals) == 2 \
            else Val(INT, c=12)
        circle = self.g.add("GeometryNodeCurvePrimitiveCircle", {"mode": "RADIUS"},
                            {"Resolution": self.inp(res, INT), "Radius": 1.0})
        inputs = {"Profile Curve": circle.out("Curve"), "Scale": self.inp(vals[0], FLOAT, "sweep()'s radius"),
                  "Fill Caps": True}
        self._geo_op(name, "GeometryNodeCurveToMesh", {}, inputs, "Curve", "Mesh", reason="sweep()")
        self.point_normals = False
        return Val(VOID)

    def g_topoints(self, node, name):
        self.args(node, name, {0})
        self._flush_effects(self.ctx)
        normal = self.g.add("GeometryNodeInputNormal")
        st = self.g.add("GeometryNodeStoreNamedAttribute", {"data_type": "FLOAT_VECTOR", "domain": "POINT"},
                        {"Geometry": self.ctx.geo, "Name": "N", "Value": normal.out("Normal")}, pure=False)
        self.ctx.geo = st.out("Geometry")
        self._geo_op(name, "GeometryNodeMeshToPoints", {"mode": "VERTICES"}, {}, "Mesh", "Points",
                     sel_key="Selection", reason="topoints()")
        self.attr_info["N"] = (VECTOR, None, "POINT")
        self.point_normals = True
        return Val(VOID)

    def g_points(self, node, name):
        (count,) = self.args(node, name, {1})
        count = self._single(self.coerce(count, INT, "points()'s count"), "the count")
        self._flush_effects(self.ctx)
        if self.selection() is not None:
            raise FormulaError("points() replaces the geometry, so it can't be inside an if")
        p = self.g.add("GeometryNodePoints", {}, {"Count": self.inp(count, INT)}, pure=False,
                       label=self.cur_text[:60] or None)
        self.ctx.geo = p.out("Geometry")
        self._new_epoch("points()")
        self.point_normals = False
        return Val(VOID)

    def g_grid(self, node, name):
        vals = self.args(node, name, {4})
        sx, sy = [self._single(self.coerce(v, FLOAT, "the grid size"), "the size") for v in vals[:2]]
        nx, ny = [self._single(self.coerce(v, INT, "the vertex count"), "the vertex count") for v in vals[2:]]
        self._flush_effects(self.ctx)
        if self.selection() is not None:
            raise FormulaError("grid() replaces the geometry, so it can't be inside an if")
        g = self.g.add("GeometryNodeMeshGrid", {}, {"Size X": self.inp(sx, FLOAT), "Size Y": self.inp(sy, FLOAT),
                                                    "Vertices X": self.inp(nx, INT), "Vertices Y": self.inp(ny, INT)},
                       pure=False, label=self.cur_text[:60] or None)
        self.ctx.geo = g.out("Mesh")
        self._new_epoch("grid()")
        self.point_normals = False
        return Val(VOID)

    def g_join(self, node, name):
        if len(node.args) != 1:
            raise FormulaError('join() takes join(1) or join(chobj("name"))')
        other, is_self = self.geo_arg(node.args[0], name)
        if is_self:
            raise FormulaError("join(0) would join the geometry with itself")
        self._flush_effects(self.ctx)
        if self.selection() is not None:
            raise FormulaError("join() can't be inside an if")
        j = self.g.add("GeometryNodeJoinGeometry", {}, {"Geometry": [self.ctx.geo, other]}, pure=False,
                       label=self.cur_text[:60] or None)
        self.ctx.geo = j.out("Geometry")
        self._new_epoch("join()")
        return Val(VOID)

    def g_transform(self, node, name):
        vals = self.args(node, name, {1, 2, 3})
        t = self.coerce(vals[0], VECTOR, "transform()'s translation")
        inputs = {"Mode": "Components", "Translation": self.inp(self._single(t, "the translation"), VECTOR)}
        if len(vals) > 1:
            r = vals[1] if vals[1].t == ROTATION else self.euler_rot(vals[1])
            inputs["Rotation"] = self.inp(self._single(r, "the rotation"), ROTATION)
        if len(vals) > 2:
            inputs["Scale"] = self.inp(self._single(self.coerce(vals[2], VECTOR, "transform()'s scale"), "the scale"),
                                       VECTOR)
        self._geo_op(name, "GeometryNodeTransform", {}, inputs, "Geometry", "Geometry", keep_elements=True)
        return Val(VOID)

    def g_setmaterial(self, node, name):
        (m,) = self.args(node, name, {1})
        if m.t != MATERIAL:
            raise FormulaError('setmaterial() takes a material parameter: setmaterial(chmat("paint"))')
        self._geo_op(name, "GeometryNodeSetMaterial", {}, {"Material": m.o}, "Geometry", "Geometry",
                     sel_key="Selection", keep_elements=True)
        return Val(VOID)

    def g_shadesmooth(self, node, name):
        (b,) = self.args(node, name, {1})
        self._geo_op(name, "GeometryNodeSetShadeSmooth", {"domain": "FACE"},
                     {"Shade Smooth": self.inp(b, BOOL, "shadesmooth()'s value")}, "Geometry", "Geometry",
                     sel_key="Selection", keep_elements=True)
        return Val(VOID)

    def g_sortpoints(self, node, name):
        vals = self.args(node, name, {1, 2})
        inputs = {"Sort Weight": self.inp(vals[0], FLOAT, "sortpoints()'s weight")}
        if len(vals) == 2:
            inputs["Group ID"] = self.inp(vals[1], INT, "sortpoints()'s group")
        self._geo_op(name, "GeometryNodeSortElements", {"domain": "POINT"}, inputs, "Geometry", "Geometry",
                     sel_key="Selection", reason="sortpoints()")
        return Val(VOID)
