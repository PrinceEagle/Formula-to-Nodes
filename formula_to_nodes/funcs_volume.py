# SPDX-License-Identifier: GPL-3.0-or-later
"""Volumes: OpenVDB grids as named fields in the geometry, Houdini-volume style.

    volumebounds({-1, -1, 0}, {1, 1, 3}, 0.04);     // the voxels to work on
    runover(voxel) {                                 // a Volume Wrangle
        f@density = max(f@density, emitter);
        v@vel += {0, 0, 1} * f@density * 4 * deltatime;
    }
    volumeproject("vel");                            // divergence-free (Poisson, Blender 5.3)
    volumeadvect("density", "vel", deltatime);       // MacCormack by default

Inside runover(voxel) { } every @name is a grid: reads sample it at the
voxel centre, writes go through one Field to Grid node into a Store Named
Grid. @P is the voxel centre, @ix @iy @iz its index. The grid operations
(advect, project, blur, curl...) run once on whole grids, which is what makes
them fast: the solver work happens inside Blender's OpenVDB nodes.
"""

import ast
import math

from .core import FLOAT, INT, BOOL, VECTOR, VOID, SOCKET_DTYPE, Val, reg, FormulaError, type_word

# Store Named Grid uses its own data type names
GRID_STORE = {FLOAT: "FLOAT", VECTOR: "VECTOR_FLOAT", INT: "INT", BOOL: "BOOLEAN"}
GRID_TYPES = (FLOAT, VECTOR, INT, BOOL)
# usual Houdini/Blender grid names and their types (used when a script reads a grid it didn't write)
KNOWN_GRIDS = {"density": FLOAT, "temperature": FLOAT, "heat": FLOAT, "flame": FLOAT, "fuel": FLOAT,
               "smoke": FLOAT, "color": VECTOR, "vel": VECTOR, "velocity": VECTOR, "v": VECTOR,
               "pressure": FLOAT, "divergence": FLOAT, "sdf": FLOAT, "surface": FLOAT, "curl": VECTOR,
               "vorticity": VECTOR, "fog": FLOAT, "mask": FLOAT, "collision": FLOAT, "speed": FLOAT}
PREFIX_GRID = {"f": FLOAT, "v": VECTOR, "i": INT, "b": BOOL}
ADVECT_SCHEMES = {"semi": "Semi-Lagrangian", "semilagrangian": "Semi-Lagrangian", "sl": "Semi-Lagrangian",
                  "midpoint": "Midpoint", "rk2": "Midpoint", "rk3": "Runge-Kutta 3", "rk4": "Runge-Kutta 4",
                  "maccormack": "MacCormack", "bfecc": "BFECC"}
KERNELS = {"constant": "Constant", "box": "Constant", "linear": "Linear", "quadratic": "Quadratic B-Spline",
           "cubic": "Cubic B-Spline"}
COMPONENTS = {"mesh": "Mesh", "meshes": "Mesh", "points": "Point Cloud", "pointcloud": "Point Cloud",
              "curve": "Curve", "curves": "Curve", "grease pencil": "Grease Pencil",
              "greasepencil": "Grease Pencil", "instances": "Instances", "instance": "Instances",
              "volume": "Volume", "volumes": "Volume"}

V = "Volumes"
reg("volumebounds", "volumebounds(min, max, voxelsize) or volumebounds(\"grid\")",
    "the voxels runover(voxel) { } writes: a box with cube voxels, or the voxels of an existing grid",
    V, "v_bounds", effect=True, feature="volume_grids")
reg("volumeadvect", 'volumeadvect("grid", "vel", dt [, "maccormack"])',
    "moves a grid along a velocity grid (schemes: semi, midpoint, rk3, rk4, maccormack, bfecc)",
    V, "v_advect", effect=True, feature="volume_grids")
reg("volumeproject", 'volumeproject("vel" [, iterations, tolerance])',
    "makes a velocity grid divergence-free (incompressible): Blender 5.3's Poisson solver, "
    "or Jacobi iterations on 5.2", V, "v_project", effect=True, feature="volume_grids")
reg("volumesolve", 'volumesolve("rhs", "out" [, iterations, tolerance])',
    "solves ∇²out = rhs (zero at the volume's edge) — pressure, diffusion, smooth interpolation",
    V, "v_solve", effect=True, feature="volume_grids")
reg("volumeblur", 'volumeblur("grid", width [, iterations])', "box blur of a grid (width in voxels)",
    V, "v_blur", effect=True, feature="volume_grids")
reg("volumecurl", 'volumecurl("vel", "out")', "curl (vorticity) of a vector grid into a new grid",
    V, "v_derivative", effect=True, feature="volume_grids", node="GeometryNodeGridCurl", tin=VECTOR, tout=VECTOR,
    sock="Curl")
reg("volumedivergence", 'volumedivergence("vel", "out")', "divergence of a vector grid into a new grid",
    V, "v_derivative", effect=True, feature="volume_grids", node="GeometryNodeGridDivergence", tin=VECTOR,
    tout=FLOAT, sock="Divergence")
reg("volumegrad", 'volumegrad("grid", "out")', "gradient of a float grid into a new vector grid",
    V, "v_derivative", effect=True, feature="volume_grids", node="GeometryNodeGridGradient", tin=FLOAT,
    tout=VECTOR, sock="Gradient")
reg("rasterize", 'rasterize("grid", value, voxelsize [, "linear"])',
    "splats a per-point value into a grid (kernels: constant, linear, quadratic, cubic)", V, "v_rasterize",
    effect=True, feature="rasterize_points")
reg("volumefrommesh", 'volumefrommesh("grid", voxelsize [, "sdf" | "fog"])',
    "the mesh as a signed distance grid (negative inside) or a fog grid (1 inside)", V, "v_from_mesh",
    effect=True, feature="volume_grids")
reg("volumefrompoints", 'volumefrompoints("grid", voxelsize [, radius])',
    "spheres around the points as one signed distance grid (radius defaults to @pscale) — metaballs, blobs",
    V, "v_from_points", effect=True, feature="volume_grids")
reg("sdfsmooth", 'sdfsmooth("grid", width [, iterations])', "smooths a signed distance grid (keeps it a distance)",
    V, "v_sdf_filter", effect=True, feature="volume_grids", node="GeometryNodeSDFGridMean")
reg("sdfoffset", 'sdfoffset("grid", distance)', "grows (positive) or shrinks a signed distance grid", V,
    "v_sdf_filter", effect=True, feature="volume_grids", node="GeometryNodeSDFGridOffset")
reg("sdffillet", 'sdffillet("grid", iterations)', "rounds the concave creases of a signed distance grid", V,
    "v_sdf_filter", effect=True, feature="volume_grids", node="GeometryNodeSDFGridFillet")
reg("sdfboolean", 'sdfboolean("a", "b", "union" | "intersect" | "difference" [, "out"])',
    "combines two signed distance grids (into a, or into out)", V, "v_sdf_boolean", effect=True,
    feature="volume_grids")
reg("volumetomesh", 'volumetomesh("grid" [, threshold, adaptivity])',
    "replaces the mesh with the grid's iso-surface (keeps volumes, points and curves)", V, "v_to_mesh",
    effect=True, feature="volume_grids")
reg("volumeremove", 'volumeremove("grid")', "deletes a grid", V, "v_remove", effect=True,
    feature="volume_grids")
reg("keep", 'keep("volume" [, "mesh", "points", "curves", "instances"])',
    "keeps only these kinds of geometry (e.g. drop the emitter mesh after a smoke sim)",
    "Geometry operations", "v_keep", effect=True)


class VolumeSpace:
    """The voxel box from volumebounds(): a topology grid plus what we know
    about it statically (for the Jacobi fallback's voxel size)."""
    __slots__ = ("topo", "voxel")

    def __init__(self, topo, voxel=None):
        self.topo, self.voxel = topo, voxel


class VolumeOps:
    # ── voxel attributes ───────────────────────────────────────────────────
    def resolve_voxel_attr(self, prefix, raw, write):
        from .compiler import Attr
        if raw in ("P", "position"):
            if write:
                raise FormulaError("can't write to '@P' here — voxels stay where they are; "
                                   "move the contents with volumeadvect()")
            return Attr("position", VECTOR, "readonly", "GeometryNodeInputPosition", "Position")
        if raw in ("ix", "iy", "iz"):
            if write:
                raise FormulaError(f"can't write to '@{raw}' — it's the voxel's index")
            return Attr(raw, INT, "voxel_index")
        if raw in ("Time", "time", "Frame", "frame"):
            node, sock = ("GeometryNodeInputSceneTime", "Seconds" if raw.lower() == "time" else "Frame")
            return Attr(raw.lower(), FLOAT, "readonly", node, sock, field=False)
        if raw in ("ptnum", "primnum", "vtxnum", "elemnum", "curvenum", "edgenum", "numpt", "numprim",
                   "numvtx", "numelem", "numedge", "numcurve") or (raw in ("N", "pscale") and not prefix):
            raise FormulaError(f"'@{raw}' belongs to geometry elements — inside runover(voxel) {{ }} "
                               f"use @P, @ix/@iy/@iz and grids (f@density, v@vel ...)")
        t = self.grid_type(raw, prefix)
        return Attr(raw, t, "voxel")

    def grid_type(self, name, prefix=None, default=FLOAT):
        if prefix:
            t = PREFIX_GRID.get(prefix)
            if t is None:
                raise FormulaError(f"grids hold floats (f@), vectors (v@), ints (i@) or bools (b@) — "
                                   f"not '{prefix}@{name}'")
            return t
        if name in self.grid_info:
            return self.grid_info[name]
        if name in KNOWN_GRIDS:
            return KNOWN_GRIDS[name]
        if default is FLOAT:
            self.note(f"'@{name}' has no type prefix, so it's a float grid — write v@{name} for a vector grid")
        return default

    def _split_grid_name(self, raw, what):
        """'v@vel' → ('vel', VECTOR); 'vel' → ('vel', None)."""
        if "@" in raw:
            pre, _, nm = raw.partition("@")
            if pre not in PREFIX_GRID or not nm:
                raise FormulaError(f"{what}: write the grid as \"name\" or \"v@name\" / \"f@name\"")
            return nm, PREFIX_GRID[pre]
        if not raw:
            raise FormulaError(f"{what} needs a grid name")
        return raw, None

    def grid_name(self, node, what, default=None):
        raw = self.str_const(node, what)
        nm, t = self._split_grid_name(raw.strip(), what)
        if t is None:
            t = self.grid_info.get(nm) or KNOWN_GRIDS.get(nm) or default
            if t is None:
                t = FLOAT
                self.note(f"{what}: '{nm}' is read as a float grid — write \"v@{nm}\" for a vector grid")
        return nm, t

    def get_grid(self, name, t, geo=None):
        n = self.g.add("GeometryNodeGetNamedGrid", {"data_type": SOCKET_DTYPE[t]},
                       {"Volume": geo if geo is not None else self.ctx.geo, "Name": name, "Remove": False})
        return n.out("Grid")

    def sample_here(self, grid, t, interp=None):
        """A grid's value at the current field position (the voxel centre inside runover(voxel))."""
        pos = self.g.add("GeometryNodeInputPosition").out("Position")
        inputs = {"Grid": grid, "Position": pos}
        if interp:
            inputs["Interpolation"] = interp
        n = self.g.add("GeometryNodeSampleGrid", {"data_type": SOCKET_DTYPE[t]}, inputs)
        return Val(t, o=n.out("Value"), field=True)

    def read_voxel_attr(self, a):
        if a.kind == "voxel_index":
            n = self.g.add("GeometryNodeInputVoxelIndex")
            return Val(INT, o=n.out({"ix": "X", "iy": "Y", "iz": "Z"}[a.name]), field=True)
        if a.t not in GRID_TYPES:
            raise FormulaError(f"grids can't hold {type_word(a.t)}s")
        pending = self.ctx.voxels.get(a.name)
        if pending is not None:          # written earlier in this block: its new value, like VEX
            return pending[1]
        return self.sample_here(self.get_grid(a.name, a.t), a.t)

    def topology_for(self, name, t):
        if self.vol_space is not None:
            return self.vol_space.topo
        self.note(f"no volumebounds() before writing '@{name}': the voxels of the existing '{name}' grid are "
                  f"used (an empty grid if there is none) — add volumebounds(min, max, voxelsize) first")
        return self.get_grid(name, t)

    def store_grid(self, name, t, grid, label=None):
        st = self.g.add("GeometryNodeStoreNamedGrid", {"data_type": GRID_STORE[t]},
                        {"Volume": self.ctx.geo, "Name": name, "Grid": grid}, pure=False, label=label)
        self.ctx.geo = st.out("Volume")
        self.grid_info[name] = t
        self.writes += 1

    def to_grid(self, topo, t, value, name="value"):
        n = self.g.add("GeometryNodeFieldToGrid", {"data_type": SOCKET_DTYPE[t]},
                       {"Topology": topo, "Field_0": self.inp(value, t)},
                       items=[(SOCKET_DTYPE[t], name)], items_attr="grid_items")
        return n.out("Grid_0")

    def write_voxel(self, attr, value):
        """Writes wait in ctx.voxels and are stored together when the block ends: one Field to
        Grid node evaluates all of them in a single pass over the voxels (shared maths is
        computed once), and later reads in the block use the new values directly."""
        t = attr.t
        if t not in GRID_TYPES:
            raise FormulaError(f"grids can't hold {type_word(t)}s")
        known = self.ctx.voxels.get(attr.name, (self.grid_info.get(attr.name),))[0]
        if known is not None and known != t:
            raise FormulaError(f"'{attr.name}' is a {type_word(known)} grid — it can't become a {type_word(t)} grid")
        sel = self.selection()
        if sel is not None:
            value = self.switch(sel, self.read_voxel_attr(attr), value, t)
        topo = self.ctx.voxels[attr.name][2] if attr.name in self.ctx.voxels else self.topology_for(attr.name, t)
        self.ctx.voxels[attr.name] = (t, value, topo)
        self.grid_info[attr.name] = t
        self.writes += 1

    def flush_voxels(self, ctx):
        if not ctx.voxels:
            return
        groups = {}
        for name, (t, value, topo) in ctx.voxels.items():
            groups.setdefault(topo.key(), (topo, []))[1].append((name, t, value))
        ctx.voxels = {}
        saved, self.ctx = self.ctx, ctx
        try:
            for topo, items in groups.values():
                node = self.g.add("GeometryNodeFieldToGrid", {"data_type": SOCKET_DTYPE[items[0][1]]},
                                  {"Topology": topo, **{f"Field_{k}": self.inp(v, t) for k, (_n, t, v) in enumerate(items)}},
                                  items=[(SOCKET_DTYPE[t], n) for n, t, _v in items], items_attr="grid_items",
                                  label="voxels: " + ", ".join(n for n, _t, _v in items)[:50])
                for k, (name, t, _v) in enumerate(items):
                    self.store_grid(name, t, node.out(f"Grid_{k}"))
                    self.writes -= 1          # counted when written
        finally:
            self.ctx = saved

    # ── helpers ────────────────────────────────────────────────────────────
    def _vol_stmt(self, name):
        if self.selection() is not None:
            raise FormulaError(f"{name}() works on whole grids, so it can't be inside an if")
        self._flush_effects(self.ctx)

    def _const_or_single(self, node, t, what):
        v = self.coerce(self.expr(node), t, what)
        if v.field:
            raise FormulaError(f"{what} must be one value for the whole volume")
        return v

    # ── volumebounds ───────────────────────────────────────────────────────
    def v_bounds(self, node, name):
        self._vol_stmt(name)
        if len(node.args) == 1:
            gname, t = self.grid_name(node.args[0], "volumebounds()")
            self.vol_space = VolumeSpace(self.get_grid(gname, t))
            return Val(VOID)
        if len(node.args) != 3:
            raise FormulaError('volumebounds() takes volumebounds(min, max, voxelsize) or volumebounds("grid")')
        lo = self._const_or_single(node.args[0], VECTOR, "volumebounds()'s min corner")
        hi = self._const_or_single(node.args[1], VECTOR, "volumebounds()'s max corner")
        vs = self._const_or_single(node.args[2], FLOAT, "volumebounds()'s voxel size")
        if vs.is_const and vs.c <= 0:
            raise FormulaError("the voxel size must be above 0")
        if lo.is_const and hi.is_const and vs.is_const:
            res = [max(1, int(math.ceil((h - l) / vs.c - 1e-6))) for l, h in zip(lo.c, hi.c)]
            if res[0] * res[1] * res[2] > 64_000_000:
                raise FormulaError(f"that's {res[0]}×{res[1]}×{res[2]} voxels — use a bigger voxel size")
            if res[0] * res[1] * res[2] > 8_000_000:
                self.note(f"{res[0]}×{res[1]}×{res[2]} voxels is heavy — a bigger voxel size will be much faster")
            top = tuple(l + r * vs.c for l, r in zip(lo.c, res))
            inputs = {"Bounds Min": tuple(float(x) for x in lo.c), "Bounds Max": top,
                      "Resolution X": res[0], "Resolution Y": res[1], "Resolution Z": res[2]}
        else:
            size = self.vmath("SUBTRACT", hi, lo)
            cells = self.vmath("CEIL", self.vmath("DIVIDE", size, self.combine([vs, vs, vs])))
            top = self.vmath("ADD", lo, self.vmath("SCALE", cells, scale=vs))
            inputs = {"Bounds Min": self.inp(lo, VECTOR), "Bounds Max": self.inp(top, VECTOR)}
            for i, axis in enumerate("XYZ"):
                inputs[f"Resolution {axis}"] = self.inp(self.coerce(self.sep(cells, i), INT), INT)
        topo = self.g.add("GeometryNodeCubeGridTopology", {}, inputs, label="volume bounds")
        self.vol_space = VolumeSpace(topo.out("Topology"), vs)
        return Val(VOID)

    # ── advect ─────────────────────────────────────────────────────────────
    def v_advect(self, node, name):
        if len(node.args) not in (3, 4):
            raise FormulaError(f"{name}() takes {name}(\"grid\", \"vel\", dt [, \"maccormack\"])")
        self._vol_stmt(name)
        gname, t = self.grid_name(node.args[0], f"{name}()'s grid")
        vname, vt = self.grid_name(node.args[1], f"{name}()'s velocity", default=VECTOR)
        if vt != VECTOR:
            raise FormulaError(f"{name}(): the velocity grid '{vname}' must be a vector grid")
        if t not in (FLOAT, VECTOR):
            raise FormulaError(f"{name}() moves float or vector grids")
        dt = self._const_or_single(node.args[2], FLOAT, f"{name}()'s time step")
        scheme = "MacCormack"
        if len(node.args) == 4:
            word = self.str_const(node.args[3], f"{name}()'s scheme").strip().lower().replace("-", "").replace(" ", "")
            if word not in ADVECT_SCHEMES:
                raise FormulaError(f"unknown advection scheme '{word}' — use " + ", ".join(sorted(set(ADVECT_SCHEMES))))
            scheme = ADVECT_SCHEMES[word]
        limiter = "Clamp" if scheme in ("MacCormack", "BFECC") else "None"
        vel = self.get_grid(vname, VECTOR)
        grid = vel if (vname == gname and t == VECTOR) else self.get_grid(gname, t)
        n = self.g.add("GeometryNodeGridAdvect", {"data_type": SOCKET_DTYPE[t]},
                       {"Grid": grid, "Velocity": vel, "Time Step": self.inp(dt, FLOAT),
                        "Integration Scheme": scheme, "Limiter": limiter}, label=self.cur_text[:60] or None)
        self.store_grid(gname, t, n.out("Grid"))
        return Val(VOID)

    # ── Poisson ────────────────────────────────────────────────────────────
    def _iter_args(self, node, start, name, default_iters):
        iters = Val(INT, c=default_iters)
        tol = Val(FLOAT, c=1e-3)
        if len(node.args) > start:
            iters = self._const_or_single(node.args[start], INT, f"{name}()'s iterations")
        if len(node.args) > start + 1:
            tol = self._const_or_single(node.args[start + 1], FLOAT, f"{name}()'s tolerance")
        return iters, tol

    def _poisson(self, rhs, topo, iters, tol, name):
        """∇²p = rhs with p = 0 outside the active voxels. Returns the p grid."""
        rhs_dense = self.to_grid(topo, FLOAT, self.sample_here(rhs, FLOAT), "rhs")
        if self.target.supports("poisson"):
            n = self.g.add("GeometryNodeGridSolvePoisson", {},
                           {"Grid": rhs_dense, "Max Iterations": self.inp(iters, INT),
                            "Error Threshold": self.inp(tol, FLOAT), "Threshold Mode": "Relative",
                            "Boundary": "Fixed"}, label="Poisson solve")
            return n.out("Solution")
        # Blender 5.2: Jacobi iterations p ← p + h²/6 (∇²p − rhs)
        if self.vol_space is None or self.vol_space.voxel is None:
            raise FormulaError(f"{name}() on Blender {self.target.version[0]}.{self.target.version[1]} iterates and "
                               f"needs the voxel size — call volumebounds(min, max, voxelsize) first "
                               f"(Blender 5.3 solves it directly)")
        self.note(f"{name}(): Blender 5.3 solves this in one Poisson node; this version runs "
                  f"{iters.c if iters.is_const else 'n'} Jacobi iterations instead (slower, less exact)")
        h2 = self.math("MULTIPLY", self.vol_space.voxel, self.vol_space.voxel)
        zi = self.g.add("GeometryNodeRepeatInput", {},
                        {"Iterations": self.inp(iters, INT), "Item_0": self.ctx.geo,
                         "Item_1": self.to_grid(topo, FLOAT, Val(FLOAT, c=0.0), "p")}, pure=False,
                        label="Jacobi pressure")
        zo = self.g.add("GeometryNodeRepeatOutput", {}, {}, pure=False)
        zi.pair = zo
        zo.items, zo.items_attr = [("GEOMETRY", "Geometry"), ("FLOAT", "p")], "repeat_items"
        p = zi.out("Item_1")
        lap = self.g.add("GeometryNodeGridLaplacian", {}, {"Grid": p}).out("Laplacian")
        upd = self.math("MULTIPLY", self.math("SUBTRACT", self.sample_here(lap, FLOAT), self.sample_here(rhs_dense, FLOAT)),
                        self.math("DIVIDE", h2, Val(FLOAT, c=6.0)))
        newp = self.to_grid(topo, FLOAT, self.math("ADD", self.sample_here(p, FLOAT), upd), "p")
        zo.inputs["Item_0"] = zi.out("Item_0")
        zo.inputs["Item_1"] = newp
        return zo.out("Item_1")

    def v_project(self, node, name):
        if not 1 <= len(node.args) <= 3:
            raise FormulaError(f"{name}() takes {name}(\"vel\" [, iterations, tolerance])")
        self._vol_stmt(name)
        vname, vt = self.grid_name(node.args[0], f"{name}()'s velocity", default=VECTOR)
        if vt != VECTOR:
            raise FormulaError(f"{name}(): '{vname}' must be a vector grid")
        iters, tol = self._iter_args(node, 1, name, 100 if self.target.supports("poisson") else 40)
        vel = self.get_grid(vname, VECTOR)
        topo = self.vol_space.topo if self.vol_space is not None else vel
        div = self.g.add("GeometryNodeGridDivergence", {}, {"Grid": vel}).out("Divergence")
        p = self._poisson(div, topo, iters, tol, name)
        grad = self.g.add("GeometryNodeGridGradient", {}, {"Grid": p}).out("Gradient")
        newv = self.vmath("SUBTRACT", self.sample_here(vel, VECTOR), self.sample_here(grad, VECTOR))
        self.store_grid(vname, VECTOR, self.to_grid(topo, VECTOR, newv, vname), label=self.cur_text[:60] or None)
        return Val(VOID)

    def v_solve(self, node, name):
        if not 2 <= len(node.args) <= 4:
            raise FormulaError(f"{name}() takes {name}(\"rhs\", \"out\" [, iterations, tolerance])")
        self._vol_stmt(name)
        rname, rt = self.grid_name(node.args[0], f"{name}()'s right-hand side", default=FLOAT)
        oname, ot = self._split_grid_name(self.str_const(node.args[1], f"{name}()'s output").strip(), name)
        if rt != FLOAT or (ot not in (None, FLOAT)):
            raise FormulaError(f"{name}() solves float grids")
        iters, tol = self._iter_args(node, 2, name, 200 if self.target.supports("poisson") else 60)
        rhs = self.get_grid(rname, FLOAT)
        topo = self.vol_space.topo if self.vol_space is not None else rhs
        self.store_grid(oname, FLOAT, self._poisson(rhs, topo, iters, tol, name), label=self.cur_text[:60] or None)
        return Val(VOID)

    # ── filters and derivatives ────────────────────────────────────────────
    def v_blur(self, node, name):
        if len(node.args) not in (2, 3):
            raise FormulaError(f"{name}() takes {name}(\"grid\", width [, iterations])")
        self._vol_stmt(name)
        gname, t = self.grid_name(node.args[0], f"{name}()'s grid")
        width = self._const_or_single(node.args[1], INT, f"{name}()'s width")
        iters = self._const_or_single(node.args[2], INT, f"{name}()'s iterations") if len(node.args) == 3 \
            else Val(INT, c=1)
        n = self.g.add("GeometryNodeGridMean", {"data_type": SOCKET_DTYPE[t]},
                       {"Grid": self.get_grid(gname, t), "Width": self.inp(width, INT),
                        "Iterations": self.inp(iters, INT)})
        self.store_grid(gname, t, n.out("Grid"), label=self.cur_text[:60] or None)
        return Val(VOID)

    def v_derivative(self, node, name):
        from .core import FUNCS
        d = FUNCS[name].data
        if len(node.args) != 2:
            raise FormulaError(f"{name}() takes {FUNCS[name].sig}")
        self._vol_stmt(name)
        gname, t = self.grid_name(node.args[0], f"{name}()'s grid", default=d["tin"])
        if t != d["tin"]:
            raise FormulaError(f"{name}() needs a {type_word(d['tin'])} grid — '{gname}' is a {type_word(t)} grid")
        oname, ot = self._split_grid_name(self.str_const(node.args[1], f"{name}()'s output").strip(), name)
        if ot not in (None, d["tout"]):
            raise FormulaError(f"{name}() makes a {type_word(d['tout'])} grid")
        n = self.g.add(d["node"], {}, {"Grid": self.get_grid(gname, t)})
        self.store_grid(oname, d["tout"], n.out(d["sock"]), label=self.cur_text[:60] or None)
        return Val(VOID)

    # ── conversions ────────────────────────────────────────────────────────
    def v_rasterize(self, node, name):
        if len(node.args) not in (3, 4):
            raise FormulaError(f"{name}() takes {name}(\"grid\", value, voxelsize [, \"linear\"])")
        if self.domain not in ("POINT", "DETAIL"):
            raise FormulaError(f"{name}() splats points — run it over points")
        self._vol_stmt(name)
        gname, t0 = self._split_grid_name(self.str_const(node.args[0], f"{name}()'s grid").strip(), name)
        val = self.expr(node.args[1])
        if val.t not in (FLOAT, INT, BOOL, VECTOR):
            raise FormulaError(f"{name}() splats floats or vectors")
        t = VECTOR if val.t == VECTOR else FLOAT
        if t0 is not None and t0 != t:
            raise FormulaError(f"{name}(): the value is a {type_word(val.t)} but the grid is {type_word(t0)}")
        vs = self._const_or_single(node.args[2], FLOAT, f"{name}()'s voxel size")
        kernel = "Linear"
        if len(node.args) == 4:
            word = self.str_const(node.args[3], f"{name}()'s kernel").strip().lower()
            if word not in KERNELS:
                raise FormulaError("kernels: constant, linear, quadratic, cubic")
            kernel = KERNELS[word]
        pos = self.g.add("GeometryNodeInputPosition").out("Position")
        n = self.g.add("GeometryNodeRasterizePoints", {},
                       {"Points": self.ctx.geo, "Grid Transform Mode": "Voxel Size",
                        "Voxel Size": self.inp(vs, FLOAT), "Kernel Type": kernel, "Position": pos,
                        "Item_0": self.inp(self.coerce(val, t), t)},
                       items=[("VECTOR" if t == VECTOR else "SCALAR", gname)], items_attr="rasterize_items",
                       label=self.cur_text[:60] or None)
        self.store_grid(gname, t, n.out("Item_0"))
        return Val(VOID)

    def v_from_mesh(self, node, name):
        if len(node.args) not in (2, 3):
            raise FormulaError(f"{name}() takes {name}(\"grid\", voxelsize [, \"sdf\" | \"fog\"])")
        self._vol_stmt(name)
        gname, _t = self._split_grid_name(self.str_const(node.args[0], f"{name}()'s grid").strip(), name)
        vs = self._const_or_single(node.args[1], FLOAT, f"{name}()'s voxel size")
        kind = "sdf"
        if len(node.args) == 3:
            kind = self.str_const(node.args[2], f"{name}()'s kind").strip().lower()
            if kind not in ("sdf", "fog", "density"):
                raise FormulaError(f'{name}(): use "sdf" (signed distance) or "fog" (1 inside)')
        if kind == "sdf":
            n = self.g.add("GeometryNodeMeshToSDFGrid", {}, {"Mesh": self.ctx.geo, "Voxel Size": self.inp(vs, FLOAT),
                                                            "Band Width": 3}, label=self.cur_text[:60] or None)
            out = n.out("SDF Grid")
        else:
            n = self.g.add("GeometryNodeMeshToDensityGrid", {},
                           {"Mesh": self.ctx.geo, "Voxel Size": self.inp(vs, FLOAT), "Density": 1.0},
                           label=self.cur_text[:60] or None)
            out = n.out("Density Grid")
        self.store_grid(gname, FLOAT, out)
        return Val(VOID)

    def v_from_points(self, node, name):
        if len(node.args) not in (2, 3):
            raise FormulaError(f"{name}() takes {name}(\"grid\", voxelsize [, radius])")
        if self.domain not in ("POINT", "DETAIL"):
            raise FormulaError(f"{name}() works on points — run it over points")
        self._vol_stmt(name)
        gname, _t = self._split_grid_name(self.str_const(node.args[0], f"{name}()'s grid").strip(), name)
        vs = self._const_or_single(node.args[1], FLOAT, f"{name}()'s voxel size")
        inputs = {"Points": self.ctx.geo, "Voxel Size": self.inp(vs, FLOAT)}
        if len(node.args) == 3:
            inputs["Radius"] = self.inp(self.coerce(self.expr(node.args[2]), FLOAT, f"{name}()'s radius"), FLOAT)
        else:
            inputs["Radius"] = self.g.add("GeometryNodeInputRadius").out("Radius")
        n = self.g.add("GeometryNodePointsToSDFGrid", {}, inputs, label=self.cur_text[:60] or None)
        self.store_grid(gname, FLOAT, n.out("SDF Grid"))
        return Val(VOID)

    def v_sdf_filter(self, node, name):
        from .core import FUNCS
        idname = FUNCS[name].data["node"]
        spec = {"GeometryNodeSDFGridMean": (("Width", INT, 1), ("Iterations", INT, 1)),
                "GeometryNodeSDFGridOffset": (("Distance", FLOAT, None),),
                "GeometryNodeSDFGridFillet": (("Iterations", INT, None),)}[idname]
        need = sum(1 for s in spec if s[2] is None)
        if not 1 + need <= len(node.args) <= 1 + len(spec):
            raise FormulaError(f"{name}() takes {FUNCS[name].sig}")
        self._vol_stmt(name)
        gname, t = self.grid_name(node.args[0], f"{name}()'s grid", default=FLOAT)
        if t != FLOAT:
            raise FormulaError(f"{name}() needs a float (distance) grid")
        inputs = {"Grid": self.get_grid(gname, FLOAT)}
        for (sock, st, default), a in zip(spec, node.args[1:]):
            inputs[sock] = self.inp(self._const_or_single(a, st, f"{name}()'s {sock.lower()}"), st)
        n = self.g.add(idname, {}, inputs, label=self.cur_text[:60] or None)
        self.store_grid(gname, FLOAT, n.out("Grid"))
        return Val(VOID)

    def v_sdf_boolean(self, node, name):
        if len(node.args) not in (3, 4):
            raise FormulaError(f'{name}() takes {name}("a", "b", "union" | "intersect" | "difference" [, "out"])')
        self._vol_stmt(name)
        an, _ = self.grid_name(node.args[0], f"{name}()'s first grid", default=FLOAT)
        bn, _ = self.grid_name(node.args[1], f"{name}()'s second grid", default=FLOAT)
        op = self.str_const(node.args[2], f"{name}()'s operation").strip().lower()
        ops = {"union": "UNION", "intersect": "INTERSECT", "intersection": "INTERSECT", "difference": "DIFFERENCE",
               "subtract": "DIFFERENCE"}
        if op not in ops:
            raise FormulaError(f"{name}(): use \"union\", \"intersect\" or \"difference\"")
        out = self.str_const(node.args[3], f"{name}()'s output").strip() if len(node.args) == 4 else an
        a, b = self.get_grid(an, FLOAT), self.get_grid(bn, FLOAT)
        inputs = {"Grid 1": a, "Grid 2": [b]} if ops[op] == "DIFFERENCE" else {"Grid 2": [a, b]}
        n = self.g.add("GeometryNodeSDFGridBoolean", {"operation": ops[op]}, inputs, label=self.cur_text[:60] or None)
        self.store_grid(out, FLOAT, n.out("Grid"))
        return Val(VOID)

    def v_to_mesh(self, node, name):
        if not 1 <= len(node.args) <= 3:
            raise FormulaError(f"{name}() takes {name}(\"grid\" [, threshold, adaptivity])")
        self._vol_stmt(name)
        gname, t = self.grid_name(node.args[0], f"{name}()'s grid")
        if t != FLOAT:
            raise FormulaError(f"{name}() needs a float grid")
        thr = self._const_or_single(node.args[1], FLOAT, f"{name}()'s threshold") if len(node.args) > 1 \
            else Val(FLOAT, c=0.1)
        ad = self._const_or_single(node.args[2], FLOAT, f"{name}()'s adaptivity") if len(node.args) > 2 \
            else Val(FLOAT, c=0.0)
        mesh = self.g.add("GeometryNodeGridToMesh", {},
                          {"Grid": self.get_grid(gname, FLOAT), "Threshold": self.inp(thr, FLOAT),
                           "Adaptivity": self.inp(ad, FLOAT)}, label=self.cur_text[:60] or None)
        rest = self._components_except(("Mesh",))
        j = self.g.add("GeometryNodeJoinGeometry", {}, {"Geometry": [mesh.out("Mesh")] + rest}, pure=False)
        self.ctx.geo = j.out("Geometry")
        self._new_epoch(f"{name}()")
        self.writes += 1
        return Val(VOID)

    def _components_except(self, drop):
        sep = self.g.add("GeometryNodeSeparateComponents", {}, {"Geometry": self.ctx.geo})
        return [sep.out(c) for c in ("Mesh", "Point Cloud", "Curve", "Grease Pencil", "Instances", "Volume")
                if c not in drop]

    def v_remove(self, node, name):
        if len(node.args) != 1:
            raise FormulaError(f'{name}() takes {name}("grid")')
        self._vol_stmt(name)
        gname, t = self.grid_name(node.args[0], f"{name}()'s grid")
        n = self.g.add("GeometryNodeGetNamedGrid", {"data_type": SOCKET_DTYPE[t]},
                       {"Volume": self.ctx.geo, "Name": gname, "Remove": True}, pure=False)
        self.ctx.geo = n.out("Volume")
        self.grid_info.pop(gname, None)
        self.writes += 1
        return Val(VOID)

    def v_keep(self, node, name):
        if not node.args:
            raise FormulaError(f'{name}() takes the kinds to keep: "mesh", "points", "curves", "instances", "volume"')
        self._vol_stmt(name)
        keep = set()
        for a in node.args:
            word = self.str_const(a, f"{name}()'s kinds").strip().lower()
            if word not in COMPONENTS:
                raise FormulaError(f"{name}(): unknown kind '{word}' — use mesh, points, curves, instances or volume")
            keep.add(COMPONENTS[word])
        drop = tuple(c for c in ("Mesh", "Point Cloud", "Curve", "Grease Pencil", "Instances", "Volume")
                     if c not in keep)
        parts = self._components_except(drop)
        j = self.g.add("GeometryNodeJoinGeometry", {}, {"Geometry": parts}, pure=False, label=f"keep {', '.join(sorted(keep))}")
        self.ctx.geo = j.out("Geometry")
        self._new_epoch(f"{name}()")
        self.writes += 1
        return Val(VOID)
