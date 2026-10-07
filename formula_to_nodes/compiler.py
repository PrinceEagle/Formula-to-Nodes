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
    (Capture Attribute, on the domain it was computed in) right before the write.
  * Lexical scopes make zone boundaries safe: nothing declared inside a
    simulate/repeat/foreach block can be referenced after its '}'. Variables
    changed inside repeat/for loops and simulations are carried as zone items.
  * The run-over domain (#runover / runover(domain) { }) decides where
    attributes are stored and what @ptnum, @P, @N mean — like Houdini's
    wrangle "Run Over" menu. Detail attributes live in the geometry's bundle.
  * User functions are inlined at each call; arrays are Blender lists.
"""

import ast
import bisect
import difflib
import math
import re

from .lang import (FormulaError, parse_source, walk, split_attr_placeholder,
                   SAssign, SDecl, SReturn, SExpr, SBlock, SFor, SForeachArr)
from .core import (FLOAT, INT, BOOL, VECTOR, STRING, ROTATION, MATRIX, COLOR, FLOAT2,
                   OBJECT, GEOMETRY, VOID, RESOURCE_TYPES, TYPE_WORD, type_word, ATTR_DTYPE, READ_DTYPE,
                   SOCKET_DTYPE, ITEM_TYPE, SOCKET_TYPE, PREFIX_TYPE, Out, Node, Graph,
                   IfaceSocket, Val, FuncDef, FUNCS, reg, CATEGORY_ORDER, is_list, elem_type,
                   list_type, zero_of, quat_to_euler)
from .core import PREFIX_STORE
from . import caps, libs
from .funcs_math import MathFuncs, MATH_FOLD, fold, vfold, VM_SCALAR_OUT
from .funcs_noise import NoiseFuncs
from .funcs_geo import GeoFuncs
from .funcs_xform import XformFuncs
from .funcs_text import TextFuncs
from .funcs_lists import ListFuncs
from .funcs_params import ParamFuncs
from .geo_ops import GeoOps

__all__ = ["compile_source", "check", "CompileResult", "Compiler", "FUNCS", "CATEGORY_ORDER",
           "reference_by_category", "Out", "Node", "Graph", "IfaceSocket", "Val",
           "FLOAT", "INT", "BOOL", "VECTOR", "STRING", "ROTATION", "MATRIX"]

_RANK = {BOOL: 0, INT: 1, FLOAT: 2, VECTOR: 3}
MATH_IN = ("Value", "Value_001", "Value_002")
VM_IN = ("Vector", "Vector_001", "Vector_002")
UNROLL_MAX = 8
DOMAINS = ("POINT", "EDGE", "FACE", "CORNER", "CURVE", "INSTANCE", "LAYER", "DETAIL")
DOMAIN_WORD = {"POINT": "point", "EDGE": "edge", "FACE": "prim (face)", "CORNER": "vertex (face corner)",
               "CURVE": "curve", "INSTANCE": "instance", "LAYER": "layer", "DETAIL": "detail"}


# ═════════════════════════════════════════════════════════════════════════════
#  Scopes
# ═════════════════════════════════════════════════════════════════════════════

class Ctx:
    """A geometry chain: the root, or the inside of one zone."""
    __slots__ = ("parent", "kind", "geo", "mark", "reserved", "removals", "additions")

    def __init__(self, parent, kind, geo, mark, reserved=None):
        self.parent, self.kind, self.geo, self.mark = parent, kind, geo, mark
        self.reserved = reserved or {}
        self.removals = {}        # domain → (captured bool field, delete mode)
        self.additions = []       # geometry to join at the end of this chain


class Scope:
    __slots__ = ("parent", "ctx", "kind", "bindings", "declared", "cond", "negate", "domain", "barrier")

    def __init__(self, parent, ctx, kind, cond=None, negate=False, domain=None, barrier=False):
        self.parent, self.ctx, self.kind = parent, ctx, kind
        self.bindings, self.declared = {}, set()
        self.cond, self.negate = cond, negate
        self.domain, self.barrier = domain, barrier


class Binding:
    __slots__ = ("name", "t", "lazy", "mark", "ctx", "captures", "line", "domain", "epoch")

    def __init__(self, name, t, lazy, ctx, line, domain="POINT", epoch=0):
        self.name, self.t, self.lazy, self.ctx, self.line = name, t, lazy, ctx, line
        self.mark = ctx.mark
        self.captures = []     # [(ctx, mark, Val)]
        self.domain, self.epoch = domain, epoch


class Attr:
    __slots__ = ("name", "t", "kind", "node", "sock", "field", "store", "domain", "input", "inner")

    def __init__(self, name, t, kind, node=None, sock=None, field=True, store=None, domain=None):
        self.name, self.t, self.kind, self.node, self.sock, self.field = name, t, kind, node, sock, field
        self.store = store          # storage data type override (COLOR, FLOAT2)
        self.domain = domain        # fixed storage domain (uv → CORNER), None = run-over domain
        self.input = None           # opinput number for @opinputN_name
        self.inner = None


class _Retry(Exception):
    pass


class _FnCtx:
    __slots__ = ("fn", "nested_return")

    def __init__(self, fn):
        self.fn = fn
        self.nested_return = False


# ═════════════════════════════════════════════════════════════════════════════
#  Language tables
# ═════════════════════════════════════════════════════════════════════════════

VEX_ALIASES = {"P": "position", "N": "normal", "pscale": "radius", "v": "velocity",
               "Frame": "frame", "Time": "time", "Cd": "Cd", "rest": "rest_position",
               "material": "material_index", "matindex": "material_index"}

ALIAS_TYPES = {"P": VECTOR, "N": VECTOR, "v": VECTOR, "pscale": FLOAT, "Cd": VECTOR, "rest": VECTOR,
               "Frame": FLOAT, "Time": FLOAT, "material": INT, "matindex": INT}

# name → (node idname, output socket, type, write kind)
BUILTIN_ATTRS = {
    "position":       ("GeometryNodeInputPosition", "Position", VECTOR, "position"),
    "normal":         ("GeometryNodeInputNormal", "Normal", VECTOR, "normal"),
    "index":          ("GeometryNodeInputIndex", "Index", INT, None),
    "id":             ("GeometryNodeInputID", "ID", INT, "id"),
    "radius":         ("GeometryNodeInputRadius", "Radius", FLOAT, "radius"),
    "tilt":           ("GeometryNodeInputCurveTilt", "Tilt", FLOAT, "tilt"),
    "curveparam":     ("GeometryNodeSplineParameter", "Factor", FLOAT, None),
    "curvelength":    ("GeometryNodeSplineParameter", "Length", FLOAT, None),
    "is_cyclic":      ("GeometryNodeInputSplineCyclic", "Cyclic", BOOL, None),
    "tangent":        ("GeometryNodeInputTangent", "Tangent", VECTOR, None),
    "material_index": ("GeometryNodeInputMaterialIndex", "Material Index", INT, "material_index"),
    "area":           ("GeometryNodeInputMeshFaceArea", "Area", FLOAT, None),
    "island":         ("GeometryNodeInputMeshIsland", "Island Index", INT, None),
    "numislands":     ("GeometryNodeInputMeshIsland", "Island Count", INT, None),
    "frame":          ("GeometryNodeInputSceneTime", "Frame", FLOAT, None),
    "time":           ("GeometryNodeInputSceneTime", "Seconds", FLOAT, None),
}
NON_FIELD_BUILTINS = {"frame", "time"}
# built-ins that become plain named attributes with a known type
NAMED_ATTR_TYPES = {"rest_position": VECTOR, "velocity": VECTOR, "orient": ROTATION, "rotation": ROTATION,
                    "scale": VECTOR, "Cd": VECTOR, "Alpha": FLOAT, "uv": VECTOR, "age": FLOAT, "life": FLOAT,
                    "mass": FLOAT, "density": FLOAT, "temperature": FLOAT, "width": FLOAT,
                    "transform": MATRIX, "up": VECTOR, "nurbs_weight": FLOAT, "nurbs_order": INT}
NAMED_STORE = {"Cd": COLOR, "uv": FLOAT2}
NAMED_STORAGE_NAME = {"uv": "UVMap"}
INDEX_ALIASES = {"ptnum": "pt", "primnum": "prim", "vtxnum": "vtx", "elemnum": "elem",
                 "curvenum": "curve", "edgenum": "edge"}
COUNT_ALIASES = {"numpt": "POINT", "numprim": "FACE", "numvtx": "CORNER", "numelem": None,
                 "numedge": "EDGE", "numcurve": "CURVE"}
READONLY_HINTS = {
    "index": "the element index is implicit — store a copy instead, e.g. i@my_index = @ptnum",
    "curveparam": "it's computed from the curve — store your value under a new name",
    "curvelength": "it's computed from the curve — store your value under a new name",
    "is_cyclic": "use a Set Spline Cyclic node for that",
    "tangent": "curve tangents are computed — store your value under a new name",
    "area": "face areas are computed from the mesh",
    "island": "islands are computed from the mesh",
    "numislands": "islands are computed from the mesh",
    "frame": "scene time is read-only",
    "time": "scene time is read-only",
}

CONSTANTS = {"pi": (FLOAT, math.pi), "PI": (FLOAT, math.pi), "M_PI": (FLOAT, math.pi),
             "e": (FLOAT, math.e), "tau": (FLOAT, math.tau),
             "true": (BOOL, True), "false": (BOOL, False)}

RESERVED = {
    "deltatime": ("sim", "a simulate { } block"),
    "elemindex": ("foreach", "a foreach { } block"),
    "iteration": ("repeat", "a repeat(n) { } block"),
}

TYPE_NAMES = {"float", "int", "vector", "vector3", "vector4", "bool", "string", "matrix",
              "matrix3", "matrix4", "void"}

UNSUPPORTED = {
    "pcopen": "point cloud handles aren't available — use nearpoint(), avgof(value, group) or blur()",
    "pcfind": "point cloud lookups aren't available — use nearpoint() for the closest point",
    "pcfilter": "point cloud filtering isn't available — use blur(value, iterations) or avgof()",
    "nearpoints": "per-point lists of neighbours aren't possible in Blender — use nearpoint(), or "
                  "neighbourcount() + neighbour() for mesh neighbours",
    "neighbours": "per-point arrays aren't possible in Blender — loop: "
                  "for (int i = 0; i < 8; i++) if (i < neighbourcount(0, @ptnum)) { int n = neighbour(0, @ptnum, i); ... }",
    "primpoints": "per-prim arrays aren't possible in Blender — use primpoint(0, @primnum, i) with "
                  "primvertexcount(0, @primnum)",
    "pointprims": "per-point arrays aren't possible in Blender — use avgof() / blur() style aggregates",
    "addprim": "faces can't be created from a script — use extrude(), the mesh primitives or instance()",
    "addvertex": "vertices can't be added to faces from a script",
    "removevertex": "use removeprim() or removepoint()",
    "setattrib": "assign directly instead: f@name = value (or setpointattrib / setprimattrib)",
    "lookat3": "use lookat(from, to, up) — it returns a rotation",
    "chramp3": "use chramp() — assign it to a vector to get a colour ramp",
    "intersect_all": "only the first hit is available — use intersect()",
    "volumeindex": "use volumesample() — grids are sampled at positions",
}


# ═════════════════════════════════════════════════════════════════════════════
#  Results
# ═════════════════════════════════════════════════════════════════════════════

class CompileResult:
    def __init__(self, graph, iface, notes, mode, runover="POINT", ramps=(), target=None):
        self.graph, self.iface, self.notes, self.mode = graph, iface, notes, mode
        self.runover = runover
        self.ramps = list(ramps)       # [(name, kind)] in first-use order
        self.target = target

    @property
    def node_count(self):
        return len(self.graph.nodes)


def compile_source(source, mode="SCRIPT", output="AUTO", strict=False, runover=None,
                   target=None, resolver=None):
    """Compile or raise FormulaError. mode: 'SCRIPT' | 'FORMULA'.
    output (formula mode): 'AUTO' | 'GEOMETRY' | 'FLOAT'.
    runover: default run-over domain ('point', 'prim', ... or a domain id);
    a #runover line in the script wins. target: caps.Target or a version tuple.
    resolver(name) → text for #include (default: the bundled libraries)."""
    return Compiler(strict=strict, target=target, runover=runover, resolver=resolver).run(source, mode, output)


def check(source, mode="SCRIPT", output="AUTO", strict=False, runover=None, target=None, resolver=None):
    """(ok, error_or_None, notes). Never raises for user mistakes."""
    try:
        res = compile_source(source, mode, output, strict, runover, target, resolver)
    except FormulaError as e:
        return False, e, []
    return True, None, res.notes


def normalize_runover(word):
    from .lang import RUNOVER_DOMAINS
    if not word:
        return "POINT"
    w = str(word)
    if w.upper() in DOMAINS:
        return w.upper()
    if w.lower() in RUNOVER_DOMAINS:
        return RUNOVER_DOMAINS[w.lower()]
    raise FormulaError(f"unknown run-over domain '{word}'")


# ═════════════════════════════════════════════════════════════════════════════
#  Compiler
# ═════════════════════════════════════════════════════════════════════════════

class Compiler(MathFuncs, NoiseFuncs, GeoFuncs, XformFuncs, TextFuncs, ListFuncs, ParamFuncs, GeoOps):
    def __init__(self, strict=False, target=None, runover=None, resolver=None):
        self.strict = strict
        self.target = target if isinstance(target, caps.Target) else caps.Target(target)
        self.default_domain = normalize_runover(runover)
        self.resolver = resolver or libs.resolve
        self.g = Graph()
        self.notes = []
        self._noted = set()
        self.params = {}          # key → IfaceSocket (first-use order)
        self.attr_info = {}       # attribute name → (type, storage type, domain) written by this script
        self.detail_attrs = {}    # detail attribute name → type
        self.reads = {}           # name → sorted statement ids that read it
        self.zone_limit = float("inf")
        self.loop_ranges = []     # [(start_sid, end_sid)] of loops being compiled
        self._mark = 0
        self.cur_line = None
        self.cur_sid = -1
        self.cur_text = ""
        self.cur_src = None
        self.writes = 0
        self.return_val = None
        self.closed_names = {}    # local name → (block word, line) after its block closed
        self.functions = {}
        self.fn = None            # _FnCtx while inlining a user function
        self.call_stack = []
        self._fn_reads = {}
        self.epoch = 0
        self.epoch_reason = ""
        self.keep = []            # nodes that must survive pruning (warnings)
        self.ramps = []           # [(name, kind)]
        self.uses = set()         # features/attributes used, e.g. "pscale"
        self._hint = None         # (ast node, expected type) for chramp-style overloads
        self._stmt_call = False
        self._call_is_stmt = False
        self._uid = 0
        self.point_normals = False    # after scatter()/topoints(): @N is the stored "N" attribute
        self.wrote_pscale = False

    # ── entry ──────────────────────────────────────────────────────────────
    def run(self, source, mode, output):
        if not source or not source.strip():
            raise FormulaError("the script is empty")
        prog = parse_source(source, self.resolver)
        self.functions = prog.functions
        if prog.runover:
            self.default_domain = prog.runover
        stmts = prog.stmts
        if not stmts:
            raise FormulaError("the script is empty — it only contains comments" +
                               (" and function definitions" if self.functions else ""))

        self.geo_node = self.g.add("NodeGroupInput", visible="param:Geometry")
        self.geo_in = self.geo_node.out("param:Geometry")
        self.root_ctx = Ctx(None, "root", self.geo_in, self._new_mark())
        self.scope = self.root_scope = Scope(None, self.root_ctx, "root", domain=self.default_domain)
        self.ctx = self.root_ctx
        self.reads = self._compute_reads(stmts)

        expression_mode = (len(stmts) == 1 and isinstance(stmts[0], SExpr)
                           and not self._is_effect_stmt(stmts[0]))
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
            for i, s in enumerate(stmts):
                if isinstance(s, SReturn) and i != len(stmts) - 1:
                    raise FormulaError("return must be the last statement", s.line)
            self.stmts(stmts)

        return self._finish(mode)

    def _finish(self, mode):
        self._flush_effects(self.root_ctx)
        wrote = self.root_ctx.geo is not self.geo_in
        if not wrote and self.return_val is None and not self.keep:
            raise FormulaError("nothing to build — assign to an attribute (f@name = ...) or return a value")
        inputs = {}
        if wrote or (self.keep and self.return_val is None):
            inputs["out:Geometry"] = self.root_ctx.geo
        rtype = None
        if self.return_val is not None:
            rv = self.return_val
            if is_list(rv.t) or rv.t in RESOURCE_TYPES:
                raise FormulaError(f"the result can't be {type_word(rv.t)}")
            rtype = rv.t
            inputs["out:Result"] = rv.o if rv.o is not None else self.const_out(rv).o
        gout = self.g.add("NodeGroupOutput", inputs=inputs, pure=False)
        self.g.prune([gout] + self.keep)

        iface = []
        if "out:Geometry" in inputs:
            iface.append(IfaceSocket("out:Geometry", "OUTPUT", "Geometry", GEOMETRY))
        if rtype:
            iface.append(IfaceSocket("out:Result", "OUTPUT", "Result", rtype))
        live_keys = {n.visible for n in self.g.nodes if n.idname == "NodeGroupInput"}
        if "param:Geometry" in live_keys or "out:Geometry" in inputs:
            iface.append(IfaceSocket("param:Geometry", "INPUT", "Geometry", GEOMETRY))
        for key, p in self.params.items():
            if p.key in live_keys:
                iface.append(p)
            else:
                self.note(f"parameter '{p.name}' isn't used by anything, so it was left out")
        live_ramps = {n.ramp[1] for n in self.g.nodes if n.ramp}
        ramps = [r for r in self.ramps if r[0] in live_ramps]
        return CompileResult(self.g, iface, self.notes, mode, self.default_domain, ramps, self.target)

    # ── bookkeeping ────────────────────────────────────────────────────────
    def _new_mark(self):
        self._mark += 1
        return self._mark

    def _new_uid(self):
        self._uid += 1
        return self._uid

    def note(self, msg):
        if msg not in self._noted:
            self._noted.add(msg)
            where = ""
            if self.cur_line is not None:
                where = f"{self.cur_src}, line {self.cur_line}: " if self.cur_src else f"Line {self.cur_line}: "
            self.notes.append(where + msg)

    def require(self, feature):
        if not self.target.supports(feature):
            raise FormulaError(self.target.why_not(feature))

    def _at(self, s):
        self.cur_line, self.cur_sid, self.cur_text = s.line, s.sid, getattr(s, "text", "")
        self.cur_src = getattr(s, "src", None)

    @staticmethod
    def _locate(e, s):
        if e.line is None:
            e.line = s.line
            if e.source is None:
                e.source = getattr(s, "src", None)
        return e

    def _compute_reads(self, stmts):
        reads = {}

        def add(name, sid):
            reads.setdefault(name, []).append(sid)

        for s in walk(stmts):
            exprs = list(s.exprs())
            names = {n.id for e in exprs for n in ast.walk(e) if isinstance(n, ast.Name)}
            for nm in names:
                add(nm, s.sid)
            if isinstance(s, SForeachArr):
                add(s.name, s.sid)
            if isinstance(s, SBlock) and s.kind == "if":
                cname = f"if#{s.sid}"
                inner = list(walk(s.body)) + (list(walk(s.orelse)) if s.orelse else [])
                for st in inner:
                    if isinstance(st, SBlock) and st.kind != "if":
                        add(cname, st.sid)
                    elif isinstance(st, SExpr):
                        add(cname, st.sid)
                    elif isinstance(st, SAssign):
                        base = st.target if isinstance(st.target, ast.Name) else st.target.value
                        if split_attr_placeholder(base.id) or self._calls_user_fn(st):
                            add(cname, st.sid)
                        else:
                            add(cname, s.end_sid + 0.5)
                    elif isinstance(st, SDecl) and self._calls_user_fn(st):
                        add(cname, st.sid)
        for v in reads.values():
            v.sort()
        return reads

    def _calls_user_fn(self, s):
        for e in s.exprs():
            for n in ast.walk(e):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in self.functions:
                    return True
        return False

    def _read_between(self, name, lo, hi):
        """Is ``name`` read by a statement with lo < sid <= hi (or anywhere in
        a loop being compiled, because the next iteration reads it again)?"""
        sids = self.reads.get(name)
        if not sids:
            return False
        i = bisect.bisect_right(sids, lo)
        if i < len(sids) and sids[i] <= hi:
            return True
        for start, end in self.loop_ranges:
            j = bisect.bisect_left(sids, start)
            if j < len(sids) and sids[j] <= end:
                return True
        return False

    # ── run-over domain ────────────────────────────────────────────────────
    @property
    def domain(self):
        sc = self.scope
        while sc is not None:
            if sc.domain:
                return sc.domain
            sc = sc.parent
        return self.default_domain

    def field_domain(self):
        d = self.domain
        return "POINT" if d == "DETAIL" else d

    # ── statements ─────────────────────────────────────────────────────────
    def stmts(self, body):
        """Compile a block. Returns 'returned' if it ends in an unconditional
        return (inside a function), 'maybe' if it may return early."""
        i = 0
        while i < len(body):
            s = body[i]
            self._at(s)
            try:
                flow = self.stmt(s)
            except FormulaError as e:
                raise self._locate(e, s) from None
            rest = body[i + 1:]
            if flow == "returned":
                if rest:
                    self._at(rest[0])
                    self.note("the lines after this return never run")
                return "returned"
            if flow == "maybe" and rest:
                self._guard_rest(rest)
                return "maybe"
            if flow == "maybe":
                return "maybe"
            i += 1
        return None

    def stmt(self, s):
        if isinstance(s, SDecl):
            self.s_decl(s)
        elif isinstance(s, SAssign):
            self.s_assign(s)
        elif isinstance(s, SReturn):
            return self.s_return(s)
        elif isinstance(s, SExpr):
            self.s_expr(s)
        elif isinstance(s, SFor):
            self.s_for(s)
        elif isinstance(s, SForeachArr):
            self.s_forarr(s)
        elif isinstance(s, SBlock):
            if s.kind == "if":
                return self.s_if(s)
            if s.kind == "runover":
                return self.s_runover(s)
            if s.kind == "init":
                raise FormulaError("init { } only works at the start of a simulate { } block")
            self.s_zone(s)
        return None

    def _is_effect_stmt(self, s):
        v = s.value
        if not (isinstance(v, ast.Call) and isinstance(v.func, ast.Name)):
            return False
        name = v.func.id
        if name in self.functions:
            return True
        fd = FUNCS.get(name)
        return fd is not None and fd.effect

    def s_expr(self, s):
        if not self._is_effect_stmt(s):
            raise FormulaError(
                f"'{s.text}' doesn't do anything — assign it to an attribute "
                f"(f@name = ...) or a variable, or use return")
        self._stmt_call = True
        try:
            self.expr(s.value)
        finally:
            self._stmt_call = False

    def s_return(self, s):
        if self.fn is not None:
            return self._fn_return(s)
        if self.scope is not self.root_scope:
            raise FormulaError("return must be at the top level, not inside a block")
        if s.value is None:
            raise FormulaError("return needs a value, e.g. return length(v@position)")
        v = self.expr(s.value)
        if v.t in RESOURCE_TYPES or is_list(v.t):
            raise FormulaError(f"return can't return {type_word(v.t)}")
        self.return_val = v
        return None

    def s_decl(self, s):
        self._check_new_name(s.name)
        if s.name in self.scope.declared:
            raise FormulaError(f"'{s.name}' is already declared in this block — assign to it instead: {s.name} = ...")
        if is_list(s.vtype):
            self.require("lists")
            val = self.array_value(s.value, elem_type(s.vtype), f"'{s.name}'")
        elif s.value is None:
            val = Val(s.vtype, c=zero_of(s.vtype))
        else:
            val = self.coerce(self.expr_hint(s.value, s.vtype), s.vtype,
                              f"'{s.name}' (declared {type_word(s.vtype)})")
        self._declare(s.name, s.vtype, val)

    def expr_hint(self, node, t):
        """Evaluate ``node`` knowing the type it will be stored as (lets
        chramp() return a colour, {..} literals become arrays, ...)."""
        saved = self._hint
        self._hint = (node, t)
        try:
            return self.expr(node)
        finally:
            self._hint = saved

    def hinted(self, node):
        """Expected type if ``node`` is the expression being hinted, else None."""
        return self._hint[1] if self._hint is not None and self._hint[0] is node else None

    def _declare(self, name, t, val):
        if any(p.name == name and p.key == f"param:{name}" and not p.explicit for p in self.params.values()):
            raise FormulaError(f"'{name}' was already used as a slider before this line — "
                               f"declare it before using it, or pick another name")
        self.scope.bindings[name] = Binding(name, t, val, self.ctx, self.cur_line,
                                            self.field_domain(), self.epoch)
        self.scope.declared.add(name)

    def _check_new_name(self, name):
        if name in CONSTANTS or name in RESERVED:
            raise FormulaError(f"'{name}' is a built-in name and can't be used as a variable")
        if name in FUNCS:
            raise FormulaError(f"'{name}' is a function name — pick another variable name")
        if name in self.functions:
            raise FormulaError(f"'{name}' is one of your functions — pick another variable name")
        if name in TYPE_NAMES:
            raise FormulaError(f"'{name}' is a type name")
        if name.startswith("__"):
            raise FormulaError("names starting with __ are reserved")
        if split_attr_placeholder(name):
            raise FormulaError("attribute names can't be declared — assign them directly: f@name = ...")

    def s_assign(self, s):
        base, comp = self._target_parts(s.target)
        info = split_attr_placeholder(base)
        if info:
            if isinstance(comp, tuple):
                if comp[0] == "index":
                    raise FormulaError("attribute components are .x .y .z or [0] [1] [2]")
                comp = comp[1]
            self._write_attribute(info, comp, s)
        else:
            self._assign_local(base, comp, s)

    @staticmethod
    def _target_parts(t):
        """(base name, component) where component is None, 0..2 (vector) or
        ('index', ast) for array elements."""
        if isinstance(t, ast.Name):
            return t.id, None
        if isinstance(t, ast.Attribute):
            if t.attr not in ("x", "y", "z"):
                raise FormulaError(f"unknown component '.{t.attr}' — use .x, .y or .z")
            return t.value.id, "xyz".index(t.attr)
        idx = t.slice
        if isinstance(idx, ast.Slice):
            raise FormulaError("can't assign to a slice")
        if isinstance(idx, ast.Constant) and isinstance(idx.value, int) and not isinstance(idx.value, bool) \
                and 0 <= idx.value <= 2:
            return t.value.id, ("maybe", idx.value)
        return t.value.id, ("index", idx)

    def _rhs(self, s, current, hint=None):
        rhs = self.expr_hint(s.value, hint) if hint else self.expr(s.value)
        if s.op is None:
            return rhs
        return self.binop(s.op, current(), rhs)

    # attribute writes ───────────────────────────────────────────────────────
    def _write_attribute(self, info, comp, s):
        prefix, raw = info
        attr = self.resolve_attr(prefix, raw, write=True)
        label = raw if raw == attr.name else f"{raw} ({attr.name})"
        if attr.kind in ("readonly", "const", "count", "index_of", "opinput"):
            hint = READONLY_HINTS.get(attr.name, "it's read-only")
            if attr.kind == "index_of":
                hint = "the element index is implicit — store a copy instead, e.g. i@my_index = @ptnum"
            elif attr.kind == "count":
                hint = "element counts are read-only"
            elif attr.kind == "opinput":
                hint = "other inputs are read-only"
            elif attr.kind == "const":
                hint = "'up' is the constant {0, 0, 1}"
            raise FormulaError(f"can't write to '{label}' — {hint}")

        if attr.kind == "detail":
            if comp is not None:
                full = self.read_detail(attr.name, attr.t)
                parts = [self.sep(full, i) for i in range(3)]
                parts[comp] = self.coerce(self._rhs(s, lambda: parts[comp]), FLOAT, f"'{raw}.{'xyz'[comp]}'")
                val = self.combine(parts)
            else:
                val = self.coerce(self._rhs(s, lambda: self.read_detail(attr.name, attr.t), attr.t), attr.t,
                                  f"'{raw}' ({type_word(attr.t)} detail attribute)")
            self.write_detail(attr.name, attr.t, val)
            return

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
            val = self._rhs(s, lambda: self.read_attr(attr), attr.t)
            val = self.coerce(val, attr.t, f"'{raw}' ({type_word(attr.t)} attribute)")
        else:
            if attr.t != VECTOR:
                raise FormulaError(f"'.{'xyz'[comp]}' needs a vector — '{raw}' is a {type_word(attr.t)} attribute")
            full = self.read_attr(attr)
            parts = [self.sep(full, i) for i in range(3)]
            cv = self._rhs(s, lambda: parts[comp])
            parts[comp] = self.coerce(cv, FLOAT, f"'{raw}.{'xyz'[comp]}'")
            val = self.combine(parts)
        self._emit_write(attr, value=val)

    def _emit_write(self, attr, value=None, offset=None):
        dom = self.domain
        if dom == "DETAIL":
            raise FormulaError(f"'@{attr.name}' belongs to the elements, but this runs over detail (once) — "
                               f"write detail attributes like f@total = ..., or use runover(point) {{ }}")
        if value is not None and value.field and value.t == STRING:
            self.require("string_fields")
        self.capture_live(after=self.cur_sid)
        sel = self.selection()
        geo = self.ctx.geo
        label = self.cur_text[:60] or None
        kind = attr.kind
        if kind == "position":
            if dom not in ("POINT", "INSTANCE"):
                raise FormulaError(f"@P belongs to points — this runs over {DOMAIN_WORD[dom]}s. Move whole "
                                   f"elements with foreach({DOMAIN_WORD[dom].split()[0]}) {{ }} or set positions "
                                   f"in runover(point) {{ }}")
            inputs = {"Geometry": geo}
            if offset is not None:
                inputs["Offset"] = self.inp(offset, VECTOR)
            else:
                inputs["Position"] = self.inp(value, VECTOR)
            if sel is not None:
                inputs["Selection"] = self.inp(sel, BOOL)
            node = self.g.add("GeometryNodeSetPosition", {}, inputs, pure=False, label=label)
            self.ctx.geo = node.out("Geometry")
        elif kind == "normal":
            if dom not in ("POINT", "FACE", "CORNER"):
                raise FormulaError(f"custom normals can be set per point, face or vertex — not per {DOMAIN_WORD[dom]}")
            v = value if sel is None else self.switch(sel, self.read_attr(attr), value, VECTOR)
            node = self.g.add("GeometryNodeSetMeshNormal", {"mode": "FREE", "domain": dom},
                              {"Mesh": geo, "Custom Normal": self.inp(v, VECTOR)}, pure=False, label=label)
            self.ctx.geo = node.out("Mesh")
        elif kind == "id":
            if dom not in ("POINT", "INSTANCE"):
                raise FormulaError("@id belongs to points (or instances)")
            inputs = {"Geometry": geo, "ID": self.inp(value, INT)}
            if sel is not None:
                inputs["Selection"] = self.inp(sel, BOOL)
            node = self.g.add("GeometryNodeSetID", {}, inputs, pure=False, label=label)
            self.ctx.geo = node.out("Geometry")
        elif kind == "material_index":
            inputs = {"Geometry": geo, "Material Index": self.inp(value, INT)}
            if sel is not None:
                inputs["Selection"] = self.inp(sel, BOOL)
            node = self.g.add("GeometryNodeSetMaterialIndex", {}, inputs, pure=False, label=label)
            self.ctx.geo = node.out("Geometry")
        elif kind in ("nurbs_weight", "nurbs_order"):
            self.require("nurbs")
            idname, sock, t = {"nurbs_weight": ("GeometryNodeSetNURBSWeight", "Weight", FLOAT),
                               "nurbs_order": ("GeometryNodeSetNURBSOrder", "Order", INT)}[kind]
            inputs = {"Curves": geo, sock: self.inp(value, t)}
            if sel is not None:
                inputs["Selection"] = self.inp(sel, BOOL)
            node = self.g.add(idname, {}, inputs, pure=False, label=label)
            self.ctx.geo = node.out("Curves")
        elif kind == "inst_xform":
            self.write_instance_transform(attr, value, offset, sel, label)
        else:
            store_dom = attr.domain or dom
            if kind in ("radius", "tilt"):
                store_dom = "POINT"
            if kind == "radius":
                self.uses.add("pscale")
                self.wrote_pscale = True
            store_type = attr.store or attr.t
            if store_type == STRING:
                self.require("string_fields")
            inputs = {"Geometry": geo, "Name": attr.name, "Value": self.inp(value, attr.t)}
            if sel is not None:
                inputs["Selection"] = self.inp(sel, BOOL)
            node = self.g.add("GeometryNodeStoreNamedAttribute",
                              {"data_type": ATTR_DTYPE[store_type], "domain": store_dom},
                              inputs, pure=False, label=label)
            self.ctx.geo = node.out("Geometry")
            self.attr_info[attr.name] = (attr.t, store_type, store_dom)
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
            if val.t == VOID:
                raise FormulaError("that function doesn't return a value")
            self._declare(name, val.t, val)
            return

        self._check_assignable(name, found_scope)
        cur = self.read_binding(b)
        what = f"'{name}' (a {type_word(b.t)})"
        if comp is None:
            val = self.coerce(self._rhs(s, lambda: cur, b.t), b.t, what)
        elif isinstance(comp, tuple) and (is_list(b.t) or comp[0] == "index"):
            if not is_list(b.t):
                raise FormulaError(f"'{name}' isn't an array — vector components are [0], [1] or [2]")
            idx_node = comp[1] if comp[0] == "index" else ast.Constant(comp[1])
            idx = self.coerce(self.expr(idx_node), INT, "the array index")
            item = self._rhs(s, lambda: self.list_get(cur, idx))
            val = self.list_set(cur, idx, item)
        else:
            c = comp[1] if isinstance(comp, tuple) else comp
            if b.t != VECTOR:
                raise FormulaError(f"'.{'xyz'[c]}' / [{c}] needs a vector — '{name}' is a {type_word(b.t)}")
            parts = [self.sep(cur, i) for i in range(3)]
            parts[c] = self.coerce(self._rhs(s, lambda: parts[c]), FLOAT, what)
            val = self.combine(parts)
        self.set_local(name, b.t, val)

    def _check_assignable(self, name, found_scope):
        sc = self.scope
        while sc is not found_scope:
            if sc.kind == "zone":
                raise FormulaError(
                    f"can't change '{name}' inside this block because it was declared outside it "
                    f"(values can't flow back out of foreach) — declare a new variable "
                    f"or store the value in an attribute")
            sc = sc.parent

    def set_local(self, name, t, val):
        """Rebind an existing local in the current scope (if/else merging and
        loops pick the new binding up)."""
        self.scope.bindings[name] = Binding(name, t, val, self.ctx, self.cur_line,
                                            self.field_domain(), self.epoch)

    # blocks ────────────────────────────────────────────────────────────────
    def s_if(self, s):
        cond = self.as_bool(self.expr(s.arg), "the if condition")
        cname = f"if#{s.sid}" if self.fn is None else f"if#f{self._new_uid()}"
        if self.fn is not None:
            for st in list(walk(s.body)) + (list(walk(s.orelse)) if s.orelse else []):
                self.reads.setdefault(cname, []).append(st.sid)
            self.reads[cname].sort()
        cond_b = Binding(cname, BOOL, cond, self.ctx, s.line, self.field_domain(), self.epoch)
        self.scope.bindings[cname] = cond_b
        if self.fn is not None and self._contains_return(s):
            self.fn.nested_return = True

        t_scope = self._push("if", cond=cond_b)
        self.stmts(s.body)
        self._pop()
        e_scope = None
        if s.orelse:
            e_scope = self._push("if", cond=cond_b, negate=True)
            self.stmts(s.orelse)
            self._pop()
        self._at(s)
        self._merge_if(cond_b, t_scope, e_scope, s.line)
        if self.fn is not None and self._contains_return(s):
            return "maybe"
        return None

    def _merge_if(self, cond_b, t_scope, e_scope, line):
        t_over = {n: b for n, b in t_scope.bindings.items()
                  if n not in t_scope.declared and not n.startswith("if#")}
        e_over = {}
        if e_scope is not None:
            e_over = {n: b for n, b in e_scope.bindings.items()
                      if n not in e_scope.declared and not n.startswith("if#")}
        names = list(t_over) + [n for n in e_over if n not in t_over]
        for name in names:
            _, outer = self.lookup(name)
            cond_now = self.read_binding(cond_b, warn=False)
            tv = self.read_binding(t_over[name]) if name in t_over else self.read_binding(outer)
            fv = self.read_binding(e_over[name]) if name in e_over else self.read_binding(outer)
            merged = self.switch(cond_now, fv, tv, outer.t)
            self.scope.bindings[name] = Binding(name, outer.t, merged, self.ctx, line,
                                                self.field_domain(), self.epoch)

    def _guard_rest(self, rest):
        """Inside a function after an 'if' that may return: the remaining
        statements only take effect while __done is false."""
        _, done = self.lookup("__done")
        cond = self.boolean("NOT", self.read_binding(done, warn=False))
        cname = f"if#g{self._new_uid()}"
        for st in walk(rest):
            self.reads.setdefault(cname, []).append(st.sid)
        self.reads[cname].sort()
        cond_b = Binding(cname, BOOL, cond, self.ctx, rest[0].line, self.field_domain(), self.epoch)
        self.scope.bindings[cname] = cond_b
        t_scope = self._push("if", cond=cond_b)
        self.stmts(rest)
        self._pop()
        self._merge_if(cond_b, t_scope, None, rest[0].line)

    @staticmethod
    def _contains_return(s):
        return any(isinstance(x, SReturn) for x in walk([s]))

    def s_runover(self, s):
        if s.arg == "DETAIL":
            self.require("geometry_bundles")
        self._push("block", domain=s.arg)
        flow = self.stmts(s.body)
        self._pop_merge()
        return flow

    def s_zone(self, s):
        if s.kind == "sim":
            return self._zone_sim(s)
        if s.kind == "repeat":
            count = self.coerce(self.expr(s.arg), INT, "the repeat count")
            if count.field:
                raise FormulaError("the repeat count must be a single number (e.g. 5 or chi(\"steps\", 5)), "
                                   "not a per-point value")
            return self._zone_repeat(s, count, s.body)
        return self._zone_foreach(s)

    @staticmethod
    def _has_effects(body):
        for st in walk(body):
            if isinstance(st, SBlock) and st.kind not in ("if", "runover"):
                return True
            if isinstance(st, SExpr):
                return True
            if isinstance(st, SAssign) and isinstance(st.target, (ast.Name, ast.Attribute, ast.Subscript)):
                base = st.target if isinstance(st.target, ast.Name) else st.target.value
                if isinstance(base, ast.Name) and split_attr_placeholder(base.id):
                    return True
            for e in st.exprs():
                for n in ast.walk(e):
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                        fd = FUNCS.get(n.func.id)
                        if fd is not None and fd.effect:
                            return True
        return False

    def _fn_has_effects(self, fn):
        if self._has_effects(fn.body):
            return True
        for st in walk(fn.body):
            for e in st.exprs():
                for n in ast.walk(e):
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in self.functions:
                        if any(self._fn_has_effects(f) for f in self.functions[n.func.id] if f is not fn):
                            return True
        return False

    def _carried_names(self, body, exclude=()):
        """Locals declared outside ``body`` that it assigns to."""
        declared_inside, names = set(), []
        for st in walk(body):
            if isinstance(st, SDecl):
                declared_inside.add(st.name)
            if isinstance(st, SFor) and isinstance(st.init, SDecl):
                declared_inside.add(st.init.name)
            if isinstance(st, SForeachArr):
                declared_inside.add(st.name)
            targets = []
            if isinstance(st, SAssign):
                targets.append(st.target)
            if isinstance(st, SFor) and isinstance(st.init, SAssign):
                targets.append(st.init.target)
            for t in targets:
                base = t if isinstance(t, ast.Name) else t.value
                if not isinstance(base, ast.Name) or split_attr_placeholder(base.id):
                    continue
                nm = base.id
                if nm in exclude or nm in declared_inside or nm in names:
                    continue
                _, b = self.lookup(nm)
                if b is not None:
                    names.append(nm)
        return names

    def _push(self, kind, cond=None, negate=False, domain=None, barrier=False):
        self.scope = Scope(self.scope, self.ctx, kind, cond, negate, domain, barrier)
        return self.scope

    _BLOCK_WORD = {"zone": "a simulate/repeat/foreach block", "if": "an if/else block",
                   "block": "a block", "loop": "a loop", "func": "a function"}

    def _pop(self):
        closing = self.scope
        for name in closing.declared:
            self.closed_names[name] = (self._BLOCK_WORD.get(closing.kind, "a block"),
                                       closing.bindings[name].line)
        self.scope = closing.parent
        return closing

    def _pop_merge(self):
        """Close a plain block: assignments to outer locals stay in effect."""
        closing = self._pop()
        for n, b in closing.bindings.items():
            if n not in closing.declared and not n.startswith("if#"):
                self.scope.bindings[n] = b
        return closing

    # zones ──────────────────────────────────────────────────────────────────
    def _enter_zone(self, ctx, limit, domain=None):
        saved = (self.ctx, self.zone_limit, self.scope)
        self.ctx, self.zone_limit = ctx, limit
        self._push("zone", domain=domain)
        return saved

    def _leave_zone(self, saved):
        self.scope = saved[2]
        self.ctx, self.zone_limit = saved[0], saved[1]

    def _zone_sim(self, s):
        body = list(s.body)
        while body and isinstance(body[0], SBlock) and body[0].kind == "init":
            init = body.pop(0)
            self._at(init)
            flow = self.stmts(init.body)          # runs before the zone: the first frame's state
            if flow:
                raise FormulaError("return can't be used inside init { }")
        if any(isinstance(st, SBlock) and st.kind == "init" for st in body):
            raise FormulaError("init { } must come first inside simulate { }")
        self._at(s)
        parent = self.ctx
        carried = self._carried_names(body)
        label = s.text[:60] or None
        zi = self.g.add("GeometryNodeSimulationInput", {}, {"Item_0": parent.geo}, pure=False, label=label)
        zo = self.g.add("GeometryNodeSimulationOutput", {}, {}, pure=False)
        zi.pair = zo
        items, outer_vals = [], {}
        for k, name in enumerate(carried):
            _, ob = self.lookup(name)
            ov = self.read_binding(ob)
            if ov.field:
                raise FormulaError(f"'{name}' holds a per-element value — a simulation can only carry single "
                                   f"values in variables; store per-element values in an attribute instead")
            if is_list(ob.t) or ob.t in RESOURCE_TYPES:
                raise FormulaError(f"'{name}' ({type_word(ob.t)}) can't be carried through a simulation")
            zi.inputs[f"Item_{k + 1}"] = self.inp(ov, ob.t)
            items.append((ITEM_TYPE[ob.t], name))
            outer_vals[name] = ob
        if items:
            zo.items, zo.items_attr = [("GEOMETRY", "Geometry")] + items, "state_items"
        ctx = Ctx(parent, "sim", zi.out("Item_0"), self._new_mark(),
                  {"deltatime": Val(FLOAT, o=zi.out("Delta Time"))})
        saved = self._enter_zone(ctx, s.end_sid)
        try:
            for k, name in enumerate(carried):
                ob = outer_vals[name]
                self.scope.bindings[name] = Binding(name, ob.t, Val(ob.t, o=zi.out(f"Item_{k + 1}")), ctx,
                                                    s.line, self.field_domain(), self.epoch)
            flow = self.stmts(body)
            if flow:
                raise FormulaError("return can't be used inside simulate { }")
            self._flush_effects(ctx)
            for k, name in enumerate(carried):
                b = self.scope.bindings[name]
                fv = self.read_binding(b)
                if fv.field:
                    raise FormulaError(f"'{name}' became a per-element value inside the simulation — store it "
                                       f"in an attribute instead (simulations carry single values only)")
                zo.inputs[f"Item_{k + 1}"] = self.inp(fv, b.t)
            zo.inputs["Item_0"] = ctx.geo
        finally:
            self._leave_zone(saved)
        self._at(s)
        parent.geo = zo.out("Item_0")
        parent.mark = self._new_mark()
        for k, name in enumerate(carried):
            ob = outer_vals[name]
            self.set_local(name, ob.t, Val(ob.t, o=zo.out(f"Item_{k + 1}")))

    def _zone_repeat(self, s, count, body, loop_var=None):
        """Repeat zone over ``body``; locals assigned inside are carried as
        repeat items. loop_var = (name, type, start Val, step Val) binds the
        for-loop variable to start + iteration * step."""
        if self._has_effects(body):
            self.capture_live(after=s.sid - 0.5)
        exclude = {loop_var[0]} if loop_var else set()
        carried = self._carried_names(body, exclude)
        for attempt in (0, 1):
            try:
                return self._zone_repeat_once(s, count, body, carried, loop_var, force_field=attempt == 1)
            except _Retry:
                continue

    def _zone_repeat_once(self, s, count, body, carried, loop_var, force_field):
        parent = self.ctx
        label = s.text[:60] or None
        zi = self.g.add("GeometryNodeRepeatInput", {},
                        {"Iterations": self.inp(count, INT), "Item_0": parent.geo}, pure=False, label=label)
        zo = self.g.add("GeometryNodeRepeatOutput", {}, {}, pure=False)
        zi.pair = zo
        items, outer = [], {}
        for k, name in enumerate(carried):
            _, ob = self.lookup(name)
            ov = self.read_binding(ob)
            if ob.t in RESOURCE_TYPES:
                raise FormulaError(f"'{name}' can't be changed inside a loop")
            zi.inputs[f"Item_{k + 1}"] = self.inp(ov, ob.t)
            items.append((ITEM_TYPE[elem_type(ob.t)], name))
            outer[name] = (ob, ov.field or force_field)
        if items:
            zo.items, zo.items_attr = [("GEOMETRY", "Geometry")] + items, "repeat_items"
            zo.extra = {"list_items": [is_list(outer[n][0].t) for n in carried]}
        reserved = {"iteration": Val(INT, o=zi.out("Iteration"))}
        ctx = Ctx(parent, "repeat", zi.out("Item_0"), parent.mark, reserved)
        saved = self._enter_zone(ctx, s.end_sid)
        self.loop_ranges.append((s.sid, s.end_sid))
        finals = {}
        try:
            for k, name in enumerate(carried):
                ob, fld = outer[name]
                self.scope.bindings[name] = Binding(name, ob.t, Val(ob.t, o=zi.out(f"Item_{k + 1}"), field=fld),
                                                    ctx, s.line, ob.domain, self.epoch)
            if loop_var is not None:
                name, t, start, step = loop_var
                it = Val(INT, o=zi.out("Iteration"))
                v = self.math("MULTIPLY_ADD", it, step, start) if not (start.is_const and step.is_const
                                                                       and step.c == 1 and start.c == 0) else it
                self.scope.bindings[name] = Binding(name, t, self.coerce(v, t), ctx, s.line,
                                                    self.field_domain(), self.epoch)
                self.scope.declared.add(name)
            flow = self.stmts(body)
            if flow:
                raise FormulaError("return can't be used inside a loop — set a variable and return after it")
            self._flush_effects(ctx)
            for k, name in enumerate(carried):
                b = self.scope.bindings[name]
                fv = self.read_binding(b)
                ob, fld = outer[name]
                if fv.field and not fld:
                    raise _Retry()
                zo.inputs[f"Item_{k + 1}"] = self.inp(fv, ob.t)
                finals[name] = fv.field or fld
            zo.inputs["Item_0"] = ctx.geo
        finally:
            self.loop_ranges.pop()
            self._leave_zone(saved)
        self._at(s)
        parent.geo = zo.out("Item_0")
        parent.mark = ctx.mark
        for k, name in enumerate(carried):
            ob, _ = outer[name]
            self.set_local(name, ob.t, Val(ob.t, o=zo.out(f"Item_{k + 1}"), field=finals[name]))
        return None

    def _zone_foreach(self, s):
        if self._has_effects(s.body):
            self.capture_live(after=s.sid - 0.5)
        parent = self.ctx
        label = s.text[:60] or None
        zi = self.g.add("GeometryNodeForeachGeometryElementInput", {}, {"Geometry": parent.geo},
                        pure=False, label=label)
        zo = self.g.add("GeometryNodeForeachGeometryElementOutput", {"domain": s.arg}, {}, pure=False)
        zi.pair = zo
        ctx = Ctx(parent, "foreach", zi.out("Element"), parent.mark, {"elemindex": Val(INT, o=zi.out("Index"))})
        saved = self._enter_zone(ctx, s.end_sid, domain="POINT")
        try:
            flow = self.stmts(s.body)
            if flow:
                raise FormulaError("return can't be used inside foreach { }")
            self._flush_effects(ctx)
            zo.inputs["Generation_0"] = ctx.geo
        finally:
            self._leave_zone(saved)
        self._at(s)
        parent.geo = zo.out("Generation_0")
        parent.mark = ctx.mark

    # loops ──────────────────────────────────────────────────────────────────
    def s_for(self, s):
        self._push("loop")
        var = None
        if s.init is not None:
            self._at(s)
            if isinstance(s.init, SDecl):
                self.s_decl(s.init)
                var = s.init.name
            else:
                base, comp = self._target_parts(s.init.target)
                if comp is not None or split_attr_placeholder(base):
                    raise FormulaError("the loop variable must be a plain variable: for (int i = 0; ...)")
                self._assign_local(base, None, s.init)
                var = base
        if var is None:
            raise FormulaError("for (...) needs a loop variable: for (int i = 0; i < n; i++)")
        for st in walk(s.body):
            if isinstance(st, SAssign):
                base = st.target if isinstance(st.target, ast.Name) else st.target.value
                if isinstance(base, ast.Name) and base.id == var:
                    raise FormulaError(f"don't change the loop variable '{var}' inside the loop")
        _, vb = self.lookup(var)
        t = vb.t
        if t not in (INT, FLOAT):
            raise FormulaError("the loop variable must be an int or a float")
        start = self.read_binding(vb)
        if start.field:
            raise FormulaError("the loop must start from a single value, not a per-point value")
        op, bound = self._loop_cond(s.cond, var)
        step = self._loop_step(s.step, var, t)
        bound_v = self.coerce(self.expr(bound), t if t == FLOAT else FLOAT, "the loop bound")
        if bound_v.field:
            raise FormulaError("the loop count must be the same for every element — loop to a fixed maximum "
                               "and skip with if, e.g. for (int i = 0; i < 8; i++) if (i < count) { ... }")
        values = None
        if start.is_const and bound_v.is_const:
            values = self._simulate_loop(start.c, op, bound_v.c, step.c, t)
        if values is not None and len(values) <= UNROLL_MAX:
            self.loop_ranges.append((s.sid, s.end_sid))
            try:
                for v in values:
                    self.set_local(var, t, Val(t, c=v))
                    self._push("block")
                    flow = self.stmts(s.body)
                    if flow:
                        raise FormulaError("return can't be used inside a loop — set a variable and return after it")
                    self._pop_merge()
                    self._at(s)
            finally:
                self.loop_ranges.pop()
            end_value = values[-1] + step.c if values else start.c
            self.set_local(var, t, Val(t, c=end_value))
        else:
            if values is not None:
                count = Val(INT, c=len(values))
            else:
                count = self._loop_count(start, op, bound_v, step)
            self._zone_repeat(s, count, s.body, loop_var=(var, t, start, step))
            self.set_local(var, t, self.coerce(self.math("MULTIPLY_ADD", count, step, start), t))
        self._pop_merge()

    def _loop_cond(self, cond, var):
        if not (isinstance(cond, ast.Compare) and len(cond.ops) == 1):
            raise FormulaError(f"the loop condition must compare '{var}' with a bound, e.g. {var} < 10")
        left, right, op = cond.left, cond.comparators[0], type(cond.ops[0])
        flip = {ast.Lt: ast.Gt, ast.LtE: ast.GtE, ast.Gt: ast.Lt, ast.GtE: ast.LtE,
                ast.NotEq: ast.NotEq, ast.Eq: ast.Eq}
        if isinstance(left, ast.Name) and left.id == var:
            return op, right
        if isinstance(right, ast.Name) and right.id == var:
            return flip[op], left
        raise FormulaError(f"the loop condition must compare '{var}' with a bound, e.g. {var} < 10")

    def _loop_step(self, step, var, t):
        if step is None:
            raise FormulaError(f"the loop needs a step, e.g. {var}++")
        base, comp = self._target_parts(step.target)
        if base != var or comp is not None:
            raise FormulaError(f"the loop step must change '{var}'")
        if step.op in (ast.Add, ast.Sub):
            d = self.coerce(self.expr(step.value), FLOAT, "the loop step")
            sign = -1.0 if step.op is ast.Sub else 1.0
        elif step.op is None and isinstance(step.value, ast.BinOp) and isinstance(step.value.op, (ast.Add, ast.Sub)) \
                and isinstance(step.value.left, ast.Name) and step.value.left.id == var:
            d = self.coerce(self.expr(step.value.right), FLOAT, "the loop step")
            sign = -1.0 if isinstance(step.value.op, ast.Sub) else 1.0
        else:
            raise FormulaError(f"the loop step must add to or subtract from '{var}', e.g. {var}++ or {var} += 2")
        if not d.is_const or d.c == 0:
            raise FormulaError("the loop step must be a fixed, non-zero number")
        return Val(FLOAT, c=sign * float(d.c))

    @staticmethod
    def _simulate_loop(start, op, bound, step, t, limit=100000):
        cmp = {ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b, ast.Gt: lambda a, b: a > b,
               ast.GtE: lambda a, b: a >= b, ast.NotEq: lambda a, b: abs(a - b) > 1e-9,
               ast.Eq: lambda a, b: abs(a - b) <= 1e-9}[op]
        values, v = [], float(start)
        while cmp(v, bound):
            values.append(int(v) if t == INT else v)
            v += step
            if len(values) > limit:
                raise FormulaError("this for loop never ends — check the condition and the step")
        return values

    def _loop_count(self, start, op, bound, step):
        """Iteration count as nodes, for bounds known only at evaluation time."""
        s = step.c
        diff = self.math("SUBTRACT", bound, start)
        if s < 0:
            diff = self.neg(diff)
        per = self.math("DIVIDE", diff, Val(FLOAT, c=abs(s)))
        if op in (ast.Lt, ast.Gt) and ((op is ast.Lt) == (s > 0)):
            n = self.math("CEIL", per)
        elif op in (ast.LtE, ast.GtE) and ((op is ast.LtE) == (s > 0)):
            n = self.math("ADD", self.math("FLOOR", self.math("ADD", per, Val(FLOAT, c=1e-6))), Val(FLOAT, c=1.0))
        elif op is ast.NotEq:
            n = self.math("ROUND", per)
        else:
            raise FormulaError("this for loop never runs or never ends — check the condition and the step")
        return self.coerce(self.math("MAXIMUM", n, Val(FLOAT, c=0.0)), INT)

    def s_forarr(self, s):
        self.require("lists")
        arr = self.expr(s.arg)
        if not is_list(arr.t):
            raise FormulaError(f"foreach (... ; x) needs an array, but got a {type_word(arr.t)}")
        if arr.field:
            raise FormulaError("the array must be the same for every element")
        et = elem_type(arr.t)
        if s.vtype != et:
            arr_item_t = s.vtype
        else:
            arr_item_t = et
        n = arr.c if isinstance(arr.c, int) else None
        self._push("loop")
        if n is not None and n <= UNROLL_MAX:
            self.loop_ranges.append((s.sid, s.end_sid))
            try:
                for k in range(n):
                    item = self.coerce(self.list_get(arr, Val(INT, c=k)), arr_item_t, f"'{s.name}'")
                    self._push("block")
                    self._declare(s.name, arr_item_t, item)
                    flow = self.stmts(s.body)
                    if flow:
                        raise FormulaError("return can't be used inside a loop — set a variable and return after it")
                    self._pop_merge()
                    self._at(s)
            finally:
                self.loop_ranges.pop()
        else:
            count = self.list_len(arr)
            self._zone_foreach_array(s, arr, count, arr_item_t)
        self._pop_merge()

    def _zone_foreach_array(self, s, arr, count, item_t):
        if self._has_effects(s.body):
            self.capture_live(after=s.sid - 0.5)
        carried = self._carried_names(s.body, exclude={s.name})
        for attempt in (0, 1):
            try:
                return self._zone_repeat_items(s, arr, count, item_t, carried, attempt == 1)
            except _Retry:
                continue

    def _zone_repeat_items(self, s, arr, count, item_t, carried, force_field):
        # a repeat zone whose loop variable is arr[iteration]
        parent = self.ctx
        zi = self.g.add("GeometryNodeRepeatInput", {},
                        {"Iterations": self.inp(count, INT), "Item_0": parent.geo}, pure=False,
                        label=s.text[:60] or None)
        zo = self.g.add("GeometryNodeRepeatOutput", {}, {}, pure=False)
        zi.pair = zo
        items, outer = [], {}
        for k, name in enumerate(carried):
            _, ob = self.lookup(name)
            ov = self.read_binding(ob)
            zi.inputs[f"Item_{k + 1}"] = self.inp(ov, ob.t)
            items.append((ITEM_TYPE[elem_type(ob.t)], name))
            outer[name] = (ob, ov.field or force_field)
        if items:
            zo.items, zo.items_attr = [("GEOMETRY", "Geometry")] + items, "repeat_items"
            zo.extra = {"list_items": [is_list(outer[n][0].t) for n in carried]}
        ctx = Ctx(parent, "repeat", zi.out("Item_0"), parent.mark, {"iteration": Val(INT, o=zi.out("Iteration"))})
        saved = self._enter_zone(ctx, s.end_sid)
        self.loop_ranges.append((s.sid, s.end_sid))
        finals = {}
        try:
            for k, name in enumerate(carried):
                ob, fld = outer[name]
                self.scope.bindings[name] = Binding(name, ob.t, Val(ob.t, o=zi.out(f"Item_{k + 1}"), field=fld),
                                                    ctx, s.line, ob.domain, self.epoch)
            item = self.coerce(self.list_get(arr, Val(INT, o=zi.out("Iteration"))), item_t, f"'{s.name}'")
            self._declare(s.name, item_t, item)
            flow = self.stmts(s.body)
            if flow:
                raise FormulaError("return can't be used inside a loop — set a variable and return after it")
            self._flush_effects(ctx)
            for k, name in enumerate(carried):
                b = self.scope.bindings[name]
                fv = self.read_binding(b)
                ob, fld = outer[name]
                if fv.field and not fld:
                    raise _Retry()
                zo.inputs[f"Item_{k + 1}"] = self.inp(fv, ob.t)
                finals[name] = fv.field or fld
            zo.inputs["Item_0"] = ctx.geo
        finally:
            self.loop_ranges.pop()
            self._leave_zone(saved)
        self._at(s)
        parent.geo = zo.out("Item_0")
        parent.mark = ctx.mark
        for k, name in enumerate(carried):
            ob, _ = outer[name]
            self.set_local(name, ob.t, Val(ob.t, o=zo.out(f"Item_{k + 1}"), field=finals[name]))

    # user functions ─────────────────────────────────────────────────────────
    def call_user(self, node, name):
        overloads = self.functions[name]
        fn = next((f for f in overloads if len(f.params) == len(node.args)), None)
        if fn is None:
            counts = " or ".join(str(len(f.params)) for f in overloads)
            raise FormulaError(f"{name}() takes {counts} argument(s)")
        if node.keywords:
            raise FormulaError(f"{name}() doesn't take named arguments")
        if fn in self.call_stack:
            raise FormulaError(f"{name}() calls itself — recursion isn't supported")
        if len(self.call_stack) > 32:
            raise FormulaError("functions are nested too deeply")
        args = []
        for (pt, pn), a in zip(fn.params, node.args):
            v = self.expr(a)
            if is_list(pt):
                if not is_list(v.t):
                    raise FormulaError(f"{name}(): '{pn}' must be an array")
                v = self.coerce(v, pt)
            else:
                v = self.coerce(v, pt, f"{name}()'s '{pn}'")
            args.append(v)
        has_effects = self._fn_has_effects(fn)
        if has_effects:
            self.capture_live(after=self.cur_sid)

        saved = (self.reads, self.zone_limit, self.loop_ranges, self.cur_line, self.cur_sid, self.cur_text,
                 self.cur_src, self.closed_names, self.fn)
        reads = self._fn_reads.get(id(fn))
        if reads is None:
            reads = self._fn_reads[id(fn)] = self._compute_reads(fn.body)
        self.reads, self.zone_limit, self.loop_ranges = {k: list(v) for k, v in reads.items()}, float("inf"), []
        self.closed_names = {}
        self.fn = _FnCtx(fn)
        self.call_stack.append(fn)
        caller_scope = self.scope
        fscope = self._push("func", barrier=True)
        try:
            for (pt, pn), v in zip(fn.params, args):
                fscope.bindings[pn] = Binding(pn, pt, v, self.ctx, fn.line, self.field_domain(), self.epoch)
                fscope.declared.add(pn)
            initial = {pn: fscope.bindings[pn] for _, pn in fn.params}
            if fn.rtype != VOID:
                fscope.bindings["__ret"] = Binding("__ret", fn.rtype, self._zero_val(fn.rtype), self.ctx, fn.line,
                                                   self.field_domain(), self.epoch)
                fscope.declared.add("__ret")
            fscope.bindings["__done"] = Binding("__done", BOOL, Val(BOOL, c=False), self.ctx, fn.line,
                                                self.field_domain(), self.epoch)
            fscope.declared.add("__done")
            try:
                flow = self.stmts(fn.body)
            except FormulaError as e:
                if not e.message.startswith("in "):
                    e.message = f"in {name}(): {e.message}"
                raise
            if fn.rtype != VOID and flow != "returned" and not self.fn.nested_return:
                raise FormulaError(f"function '{name}' must end with a return statement", fn.line, fn.src)
            result = None
            if fn.rtype != VOID:
                result = self.read_binding(fscope.bindings["__ret"])
            changed = {pn: fscope.bindings[pn] for _, pn in fn.params
                       if fscope.bindings.get(pn) is not initial[pn]}
        finally:
            self.scope = caller_scope
            self.call_stack.pop()
            (self.reads, self.zone_limit, self.loop_ranges, self.cur_line, self.cur_sid, self.cur_text,
             self.cur_src, self.closed_names, self.fn) = saved

        # VEX passes arguments by reference: write changed parameters back
        for (pt, pn), a in zip(fn.params, node.args):
            if pn not in changed:
                continue
            nv = self.read_binding(changed[pn])
            if isinstance(a, ast.Name):
                info = split_attr_placeholder(a.id)
                if info:
                    attr = self.resolve_attr(*info, write=True)
                    if attr.kind in ("store", "position", "normal", "radius", "tilt", "id", "material_index",
                                     "inst_xform", "detail"):
                        if attr.kind == "detail":
                            self.write_detail(attr.name, attr.t, self.coerce(nv, attr.t))
                        else:
                            self._emit_write(attr, value=self.coerce(nv, attr.t))
                    continue
                sc, b = self.lookup(a.id)
                if b is not None:
                    self._check_assignable(a.id, sc)
                    self.set_local(a.id, b.t, self.coerce(nv, b.t, f"'{a.id}'"))
        if result is None:
            return Val(VOID, c=None)
        return result

    def _fn_return(self, s):
        fn = self.fn.fn
        if any(sc.kind in ("zone", "loop") for sc in self._scopes_to_function()):
            raise FormulaError("return can't be used inside a loop or zone — set a variable and return after it")
        if s.value is None:
            if fn.rtype != VOID:
                raise FormulaError(f"'{fn.name}' returns a {type_word(fn.rtype)} — return a value")
        else:
            if fn.rtype == VOID:
                raise FormulaError(f"'{fn.name}' is void — 'return' can't have a value")
            v = self.expr_hint(s.value, fn.rtype)
            v = self.coerce(v, fn.rtype, f"{fn.name}()'s return value")
            self.set_local("__ret", fn.rtype, v)
        if self.scope.kind != "func":
            self.set_local("__done", BOOL, Val(BOOL, c=True))
        return "returned"

    def _scopes_to_function(self):
        out, sc = [], self.scope
        while sc is not None and sc.kind != "func":
            out.append(sc)
            sc = sc.parent
        return out

    def _zero_val(self, t):
        if is_list(t):
            return self.array_value(None, elem_type(t), "the result")
        return Val(t, c=zero_of(t))

    # ── bindings, captures, selection ──────────────────────────────────────
    def lookup(self, name):
        sc = self.scope
        while sc is not None:
            b = sc.bindings.get(name)
            if b is not None:
                return sc, b
            if sc.barrier:
                return None, None
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
        if b.epoch != self.epoch:
            if b.name.startswith("if#"):
                raise FormulaError(f"{self.epoch_reason} inside an if changes the whole geometry — the "
                                   f"lines after it can't stay conditional; move them after the if")
            raise FormulaError(f"'{b.name}' was computed on the geometry before {self.epoch_reason} — "
                               f"compute it again after that line (or store it in an attribute first)")
        cap = self.valid_capture(b)
        if cap is not None:
            return cap
        cur = self.field_domain()
        if b.domain != cur and b.domain != "DETAIL" and b.mark == self.ctx.mark \
                and self._ctx_within(self.ctx, b.ctx) and v.t in ITEM_TYPE and v.t != STRING:
            # computed per <b.domain>, used per <cur>: capture where it was computed,
            # Blender then interpolates it to the new domain (like attribute promotion)
            node = self.g.add("GeometryNodeCaptureAttribute", {"domain": b.domain},
                              {"Geometry": self.ctx.geo, "item:0": v.o}, pure=False,
                              items=[(ITEM_TYPE[v.t], b.name)], items_attr="capture_items",
                              label=f"Capture {b.name} per {DOMAIN_WORD[b.domain].split()[0]}")
            self.ctx.geo = node.out("Geometry")
            cv = Val(b.t, o=node.out("item:0"), field=True)
            b.captures.append((self.ctx, self.ctx.mark, cv))
            return cv
        if warn and b.mark != self.ctx.mark and not b.name.startswith(("if#", "__")):
            self.note(f"'{b.name}' reads geometry and is re-evaluated after/inside a simulate block "
                      f"(simulations can't carry captured values). If you need its original value, "
                      f"store it first: {'v' if b.t == VECTOR else type_word(b.t)[0]}@{b.name} = ...")
        return v

    def capture_live(self, after):
        """Capture every geometry-dependent local that is read again after
        statement ``after`` but before the current zone closes (a capture made
        here can't be used outside this zone anyway)."""
        batch = []
        sc = self.scope
        while sc is not None:
            for b in sc.bindings.values():
                if (b.lazy.field and b not in batch and b.epoch == self.epoch
                        and self._read_between(b.name, after, self.zone_limit)
                        and self.valid_capture(b) is None):
                    if b.lazy.t not in ITEM_TYPE or is_list(b.lazy.t) or b.lazy.t in RESOURCE_TYPES:
                        continue
                    if b.lazy.t == STRING:
                        raise FormulaError(f"'{b.name}' holds per-element text that is read after a write — "
                                           f"text can't be captured; store it in an attribute first")
                    batch.append(b)
            if sc.barrier:
                break
            sc = sc.parent
        if not batch:
            return
        by_domain = {}
        for b in batch:
            by_domain.setdefault(b.domain if b.domain != "DETAIL" else "POINT", []).append(b)
        for dom, bs in by_domain.items():
            items, inputs = [], {"Geometry": self.ctx.geo}
            for k, b in enumerate(bs):
                nm = "condition" if b.name.startswith("if#") else b.name.lstrip("_")
                items.append((ITEM_TYPE[b.t], nm))
                inputs[f"item:{k}"] = b.lazy.o
            node = self.g.add("GeometryNodeCaptureAttribute", {"domain": dom}, inputs,
                              pure=False, items=items, items_attr="capture_items",
                              label="Capture " + ", ".join(i[1] for i in items)[:50])
            self.ctx.geo = node.out("Geometry")
            for k, b in enumerate(bs):
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

    def _new_epoch(self, reason):
        """The geometry's elements changed completely (scatter, realize...):
        per-element locals from before can't be read any more."""
        self.epoch += 1
        self.epoch_reason = reason

    # ── attributes ─────────────────────────────────────────────────────────
    def resolve_attr(self, prefix, raw, write=False):
        m = re.fullmatch(r"opinput(\d)_(\w+)", raw)
        if m:
            if write:
                raise FormulaError(f"can't write to '@{raw}' — other inputs are read-only")
            inner = self.resolve_attr(prefix, m.group(2))
            a = Attr(inner.name, inner.t, "opinput")
            a.input, a.inner = int(m.group(1)), inner
            return a
        dom = self.domain
        if raw in INDEX_ALIASES:
            return Attr(INDEX_ALIASES[raw], INT, "index_of")
        if raw in COUNT_ALIASES:
            return Attr(raw, INT, "count", field=False)
        name = VEX_ALIASES.get(raw, raw)
        if prefix and name != raw and raw in ALIAS_TYPES and PREFIX_TYPE.get(prefix) != ALIAS_TYPES[raw]:
            name = raw          # f@v is a float called "v", not the velocity vector
        if dom == "INSTANCE" and name in ("orient", "rotation", "scale", "transform", "radius"):
            t = {"orient": ROTATION, "rotation": ROTATION, "scale": VECTOR,
                 "transform": MATRIX, "radius": FLOAT}[name]
            if prefix and PREFIX_TYPE.get(prefix) != t:
                raise FormulaError(f"'{prefix}@{raw}' on instances is a {type_word(t)}")
            return Attr(name, t, "inst_xform")
        if dom == "DETAIL" and name not in BUILTIN_ATTRS:
            t = self._attr_type(prefix, name, raw, detail=True)
            return Attr(name, t, "detail", field=False)
        if (name in self.detail_attrs and not write and name not in self.attr_info
                and (not prefix or PREFIX_TYPE.get(prefix) == self.detail_attrs[name])):
            # read a detail attribute this script set (writes outside detail mode stay per element)
            return Attr(name, self.detail_attrs[name], "detail", field=False)
        if name == "normal" and self.point_normals:
            return Attr("N", VECTOR, "store")
        if name in BUILTIN_ATTRS:
            node, sock, t, write_kind = BUILTIN_ATTRS[name]
            if prefix and PREFIX_TYPE.get(prefix) not in (t, None) and not (prefix == "f" and t == INT):
                if not (prefix == "i" and t == FLOAT):
                    self.note(f"'{prefix}@{raw}' is the built-in {type_word(t)} '{name}'")
            return Attr(name, t, write_kind or "readonly", node, sock, field=name not in NON_FIELD_BUILTINS)
        if name == "up" and not prefix:
            return Attr("up", VECTOR, "const", field=False)
        if name in ("nurbs_weight", "nurbs_order") and write:
            return Attr(name, NAMED_ATTR_TYPES[name], name)
        t = self._attr_type(prefix, name, raw)
        store = PREFIX_STORE.get(prefix) or NAMED_STORE.get(name)
        if name in self.attr_info and not prefix:
            store = self.attr_info[name][1] if self.attr_info[name][1] in (COLOR, FLOAT2) else store
        if t == STRING:
            self.require("string_fields")
        storage_name = NAMED_STORAGE_NAME.get(name, name)
        domain = "CORNER" if store == FLOAT2 and name == "uv" else None
        return Attr(storage_name, t, "store", store=store, domain=domain)

    def _attr_type(self, prefix, name, raw, detail=False):
        if prefix:
            return PREFIX_TYPE[prefix]
        if detail and name in self.detail_attrs:
            return self.detail_attrs[name]
        if name in NAMED_ATTR_TYPES:
            return NAMED_ATTR_TYPES[name]
        if name in self.attr_info:
            return self.attr_info[name][0]
        if self.strict:
            raise FormulaError(f"give '@{raw}' a type prefix: f@{raw} (float), v@{raw} (vector), "
                               f"i@{raw} (int), b@{raw} (bool), p@{raw} (rotation)")
        self.note(f"'@{raw}' has no type prefix, so it's a vector attribute — write f@{raw} for a float")
        return VECTOR

    def read_attr(self, a):
        if a.kind == "const":
            return Val(VECTOR, c=(0.0, 0.0, 1.0))
        if a.kind == "count":
            dom = COUNT_ALIASES[a.name] or self.field_domain()
            if a.name == "numprim" and self.domain == "CURVE":
                dom = "CURVE"
            return self.count_elements(dom, self.ctx.geo)
        if a.kind == "index_of":
            return self.read_index(a.name)
        if a.kind == "detail":
            return self.read_detail(a.name, a.t)
        if a.kind == "inst_xform":
            return self.read_instance_xform(a)
        if a.kind == "opinput":
            return self.read_opinput_attr(a)
        if a.node:
            if self.domain == "DETAIL" and a.field:
                pass   # a field in detail mode: usable in aggregates, rejected when stored
            n = self.g.add(a.node)
            if a.name == "radius":
                self.uses.add("pscale")
            return Val(a.t, o=n.out(a.sock), field=a.field)
        store = a.store or (self.attr_info.get(a.name, (None, None))[1])
        dtype = READ_DTYPE[store] if store in (COLOR, FLOAT2) else READ_DTYPE[a.t]
        n = self.g.add("GeometryNodeInputNamedAttribute", {"data_type": dtype}, {"Name": a.name})
        if a.name == "radius":
            self.uses.add("pscale")
        return Val(a.t, o=n.out("Attribute"), field=True)

    def read_index(self, which):
        dom = self.domain
        if dom == "DETAIL":
            raise FormulaError("detail mode runs once — there's no current element (use a loop or point(0, ...))")
        idx = Val(INT, o=self.g.add("GeometryNodeInputIndex").out("Index"), field=True)
        if which == "elem":
            return idx
        if which == "pt":
            if dom in ("POINT", "INSTANCE", "LAYER"):
                return idx
            if dom == "CORNER":
                n = self.g.add("GeometryNodeVertexOfCorner", {}, {"Corner Index": idx.o})
                return Val(INT, o=n.out("Vertex Index"), field=True)
            raise FormulaError(f"@ptnum is per point — this runs over {DOMAIN_WORD[dom]}s; use @primnum / @elemnum, "
                               f"or primpoint(0, @primnum, i) for a face's points")
        if which == "prim":
            if dom in ("FACE", "CURVE"):
                return idx
            if dom == "CORNER":
                n = self.g.add("GeometryNodeFaceOfCorner", {}, {"Corner Index": idx.o})
                return Val(INT, o=n.out("Face Index"), field=True)
            raise FormulaError(f"@primnum is per prim — this runs over {DOMAIN_WORD[dom]}s. A point can belong to "
                               f"several faces; use #runover prim, or @curvenum for a curve point's curve")
        if which == "vtx":
            if dom == "CORNER":
                return idx
            raise FormulaError("@vtxnum is per vertex (face corner) — use #runover vertex")
        if which == "curve":
            if dom == "CURVE":
                return idx
            if dom == "POINT":
                n = self.g.add("GeometryNodeCurveOfPoint", {}, {"Point Index": idx.o})
                return Val(INT, o=n.out("Curve Index"), field=True)
            raise FormulaError("@curvenum works per point or per curve")
        if which == "edge":
            if dom == "EDGE":
                return idx
            raise FormulaError("@edgenum is per edge — use #runover edge")
        raise FormulaError(f"unknown index '{which}'")

    def count_elements(self, domain, geo):
        n = self.g.add("GeometryNodeAttributeStatistic", {"data_type": "FLOAT", "domain": domain},
                       {"Geometry": geo, "Attribute": 1.0})
        return Val(INT, o=n.out("Sum"), field=False)

    # ── parameters ─────────────────────────────────────────────────────────
    def param(self, name, t, default=None, vmin=None, vmax=None, explicit=True, description="", single=False):
        if not name or name.split("/")[-1] in ("Geometry", "Result"):
            raise FormulaError(f"'{name}' can't be used as a parameter name")
        panel, _, short = name.rpartition("/")
        short = short.strip()
        if not short:
            raise FormulaError(f"'{name}' needs a name after the folder: \"Folder/name\"")
        key = f"param:{name}"
        p = self.params.get(key)
        if p is None:
            p = IfaceSocket(key, "INPUT", short, t, default, vmin, vmax, explicit,
                            panel=panel.strip() or None, description=description, single=single)
            self.params[key] = p
        else:
            if p.vtype != t:
                raise FormulaError(f"parameter '{name}' is used as both {type_word(p.vtype)} and {type_word(t)}")
            if explicit and not p.explicit:
                p.default, p.min, p.max, p.explicit = default, vmin, vmax, True
            elif explicit and default is not None and p.default != default:
                self.note(f"parameter '{name}' has two different defaults; using the first one")
            if description and not p.description:
                p.description = description
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
        if name in FUNCS or name in UNSUPPORTED or name in self.functions:
            raise FormulaError(f"'{name}' is a function — call it with parentheses: {name}(...)")
        if name in TYPE_NAMES:
            raise FormulaError(f"'{name}' is a type — declare variables like: {name} my_var = ...")
        if name in self.closed_names:
            where, line = self.closed_names[name]
            raise FormulaError(f"'{name}' was declared inside {where} (line {line}) and doesn't exist "
                               f"after its closing '}}' — declare it before the block instead")
        if self.fn is not None:
            raise FormulaError(f"unknown name '{name}' — functions only see their parameters and their own "
                               f"variables (pass it in as an argument)")
        return self._slider(name)

    def _slider(self, name):
        candidates = set(CONSTANTS) | set(RESERVED)
        sc = self.scope
        while sc is not None:
            candidates |= {n for n in sc.bindings if not n.startswith(("if#", "__"))}
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
        if STRING in (l.t, r.t):
            if op is ast.Add and l.t == STRING and r.t == STRING:
                return self.concat([l, r])
            raise FormulaError(f"text can't be used with {word}" +
                               (" — turn numbers into text with itoa() or sprintf()" if op is ast.Add else ""))
        if is_list(l.t) or is_list(r.t):
            return self.list_math(op, l, r)
        if ROTATION in (l.t, r.t) or MATRIX in (l.t, r.t):
            return self.xform_binop(op, l, r)
        if l.t in RESOURCE_TYPES or r.t in RESOURCE_TYPES:
            raise FormulaError(f"{type_word(l.t if l.t in RESOURCE_TYPES else r.t)}s can't be used with {word}")
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
            if v.t in (STRING, MATRIX) or v.t in RESOURCE_TYPES or is_list(v.t):
                raise FormulaError(f"can't negate {type_word(v.t)}")
            if v.t == ROTATION:
                return self.rot_invert(v)
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
        return self.switch(cond, b, a)

    def e_Attribute(self, node):
        base = self.expr(node.value)
        if base.t == ROTATION and node.attr in ("x", "y", "z", "w"):
            return self.rot_component(base, node.attr)
        if node.attr not in ("x", "y", "z"):
            raise FormulaError(f"unknown component '.{node.attr}' — use .x, .y or .z")
        if base.t != VECTOR:
            raise FormulaError(f"'.{node.attr}' needs a vector, but '{ast.unparse(node.value)}' "
                               f"is a {type_word(base.t)}")
        return self.sep(base, "xyz".index(node.attr))

    def e_Subscript(self, node):
        idx = node.slice
        base = self.expr(node.value)
        if base.t == STRING:
            return self.string_index(base, idx)
        if isinstance(idx, ast.Slice):
            raise FormulaError("slices [a:b] work on text only")
        if is_list(base.t):
            return self.list_get(base, self.coerce(self.expr(idx), INT, "the array index"))
        if base.t == VECTOR:
            if isinstance(idx, ast.Constant) and isinstance(idx.value, int) and not isinstance(idx.value, bool):
                if not 0 <= idx.value <= 2:
                    raise FormulaError("vector index must be 0, 1 or 2")
                return self.sep(base, idx.value)
            return self.vector_component(base, self.coerce(self.expr(idx), INT, "the component index"))
        if base.t == ROTATION:
            if isinstance(idx, ast.Constant) and idx.value in (0, 1, 2, 3):
                return self.rot_component(base, "xyzw"[idx.value])
        raise FormulaError(f"[index] needs a vector or an array, not a {type_word(base.t)}")

    def e_Set(self, node):
        hint = self.hinted(node)
        if hint is not None and is_list(hint):
            return self.array_value(node, elem_type(hint), "the array")
        n = len(node.elts)
        if n == 3:
            return self.combine([self.expr(e) for e in node.elts])
        if n == 4:
            return self.quat_literal([self.expr(e) for e in node.elts])
        raise FormulaError(f"vector literals need exactly 3 values {{x, y, z}} (4 for a rotation), got {n}")

    def e_Tuple(self, node):
        raise FormulaError("write vectors as {x, y, z} or set(x, y, z)")

    e_List = e_Tuple

    def e_Dict(self, node):
        hint = self.hinted(node)
        if hint is not None and is_list(hint) and not node.keys:
            return self.array_value(None, elem_type(hint), "the array")
        raise FormulaError("empty or malformed braces — vectors look like {0, 0, 1}")

    def e_Call(self, node):
        if not isinstance(node.func, ast.Name):
            raise FormulaError(f"can't call '{ast.unparse(node.func)}'")
        name = node.func.id
        if name in self.functions:
            stmt_call, self._stmt_call = self._stmt_call, False
            try:
                v = self.call_user(node, name)
            finally:
                self._stmt_call = stmt_call
            if v.t == VOID and not self._stmt_call:
                raise FormulaError(f"{name}() is void — it doesn't return a value")
            return v
        fd = FUNCS.get(name)
        if fd is None:
            if name in UNSUPPORTED:
                raise FormulaError(f"{name}() isn't supported — {UNSUPPORTED[name]}")
            if split_attr_placeholder(name):
                raise FormulaError("attributes aren't functions — remove the parentheses")
            close = difflib.get_close_matches(name, sorted(set(FUNCS) | set(self.functions)), n=3, cutoff=0.6)
            hint = f" — did you mean {', '.join(c + '()' for c in close)}?" if close else ""
            raise FormulaError(f"unknown function '{name}()'{hint}")
        if fd.feature:
            self.require(fd.feature)
        if node.keywords and not fd.data.get("keywords"):
            raise FormulaError(f"{name}() doesn't take named arguments")
        stmt_call, self._stmt_call = self._stmt_call, False
        try:
            if fd.effect and not stmt_call and not fd.data.get("expr_ok"):
                raise FormulaError(f"{name}() changes the geometry — use it as a statement on its own line")
            self._call_is_stmt = stmt_call
            return getattr(self, fd.handler)(node, name)
        finally:
            self._stmt_call = stmt_call

    # ── argument helpers ───────────────────────────────────────────────────
    def args(self, node, name, counts):
        n = len(node.args)
        if n not in counts:
            want = " or ".join(str(c) for c in sorted(counts))
            raise FormulaError(f"{name}() takes {want} argument{'s' if max(counts) != 1 else ''}: "
                               f"{FUNCS[name].sig}")
        return [self.expr(a) for a in node.args]

    def str_const(self, node, what):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        v = self.expr(node)
        if v.t == STRING and v.is_const:
            return v.c
        raise FormulaError(f"{what} must be text in quotes")

    # ═══════════════════════════════════════════════════════════════════════
    #  Node helpers (folding + CSE)
    # ═══════════════════════════════════════════════════════════════════════
    def coerce(self, v, t, what="this value"):
        if v.t == t:
            return v
        if v.t == VOID:
            raise FormulaError(f"{what}: that function doesn't return a value")
        if is_list(v.t) or is_list(t):
            if is_list(v.t) and is_list(t) and elem_type(v.t) in _RANK and elem_type(t) in _RANK:
                return self.list_convert(v, elem_type(t))
            raise FormulaError(f"{what} must be a {type_word(t)}, but got {type_word(v.t)}")
        if v.t in RESOURCE_TYPES or t in RESOURCE_TYPES:
            raise FormulaError(f"{what} must be a {type_word(t)}, but got {type_word(v.t)}")
        if v.t == STRING:
            raise FormulaError(f"{what} can't be text" + (" — use atof()/atoi() to read a number" if t in (FLOAT, INT) else ""))
        if t == STRING:
            raise FormulaError(f"{what} must be text — use itoa() or sprintf() to turn numbers into text")
        if t == ROTATION or v.t == ROTATION:
            hint = (" — build one with quaternion(angle, axis), eulertoquaternion(v) or dihedral(a, b)"
                    if t == ROTATION else " — use quaterniontoeuler(q) or qrotate(q, v)")
            raise FormulaError(f"{what} must be a {type_word(t)}, but got {type_word(v.t)}{hint}")
        if t == MATRIX or v.t == MATRIX:
            raise FormulaError(f"{what} must be a {type_word(t)}, but got {type_word(v.t)} — use maketransform() "
                               f"or cracktransform()")
        if v.t == VECTOR:
            raise FormulaError(f"{what} must be a {type_word(t)}, but got a vector — "
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
            if t == ROTATION:
                return tuple(float(x) for x in v.c)
            if t == MATRIX or is_list(t):
                return self.const_out(v).o
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
        if v.t == STRING:
            return Val(STRING, o=self.g.add("FunctionNodeInputString", {"string": str(v.c)}).out("String"))
        if v.t == ROTATION:
            return Val(ROTATION, o=self.g.add("FunctionNodeInputRotation",
                                              {"rotation_euler": tuple(float(x) for x in v.c)}).out("Rotation"))
        if v.t == MATRIX:
            return Val(MATRIX, o=self.g.add("FunctionNodeCombineTransform").out("Transform"))
        if is_list(v.t):
            return self.array_value(None, elem_type(v.t), "the array")
        return Val(VECTOR, o=self.g.add("FunctionNodeInputVector",
                                         {"vector": tuple(float(x) for x in v.c)}).out("Vector"))

    def math(self, op, *args, what=None):
        word = what or f"the {op.lower().replace('_', ' ')} input"
        vals = [self.coerce(a, FLOAT, word) for a in args]
        if all(v.is_const for v in vals) and op in MATH_FOLD:
            r = fold(MATH_FOLD[op], *[v.c for v in vals])
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
        if op == "MULTIPLY_ADD" and len(vals) == 3:
            a, b, c = vals
            if b.is_const and b.c == 1.0:
                return self.math("ADD", a, c)
            if c.is_const and c.c == 0.0:
                return self.math("MULTIPLY", a, b)
        n = self.g.add("ShaderNodeMath", {"operation": op},
                       {MATH_IN[i]: self.inp(v, FLOAT) for i, v in enumerate(vals)})
        return Val(FLOAT, o=n.out("Value"), field=any(v.field for v in vals))

    def vmath(self, op, *vecs, scale=None):
        vv = [self.coerce(v, VECTOR) for v in vecs]
        s = self.coerce(scale, FLOAT, "the scale") if scale is not None else None
        if all(v.is_const for v in vv) and (s is None or s.is_const):
            r = fold(vfold, op, [v.c for v in vv], s.c if s else None)
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

    def vector_component(self, v, idx):
        """v[i] with an index only known at evaluation time."""
        if idx.is_const:
            if not 0 <= int(idx.c) <= 2:
                raise FormulaError("vector index must be 0, 1 or 2")
            return self.sep(v, int(idx.c))
        parts = [self.sep(v, i) for i in range(3)]
        return self.index_switch(idx, parts, FLOAT)

    def index_switch(self, idx, values, t):
        n = self.g.add("GeometryNodeIndexSwitch", {"data_type": SOCKET_DTYPE[t]},
                       {"Index": self.inp(idx, INT), **{f"Item_{k}": self.inp(v, t) for k, v in enumerate(values)}},
                       items=[(SOCKET_DTYPE[t], str(k)) for k in range(len(values))],
                       items_attr="index_switch_items")
        return Val(t, o=n.out("Output"), field=idx.field or any(v.field for v in values))

    def as_bool(self, v, what="the condition"):
        if v.t == BOOL:
            return v
        if v.t == VECTOR:
            raise FormulaError(f"{what} is a vector — compare it instead, e.g. length(v) > 0")
        if v.t not in (FLOAT, INT):
            raise FormulaError(f"{what} can't be {type_word(v.t)}")
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
            return self.string_compare(op, a, b)
        for v in (a, b):
            if v.t in (ROTATION, MATRIX) or v.t in RESOURCE_TYPES or is_list(v.t):
                raise FormulaError(f"{type_word(v.t)} values can't be compared")
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
        if t is None:
            if false_v.t == true_v.t:
                t = false_v.t
            elif false_v.t in _RANK and true_v.t in _RANK:
                t = max((false_v.t, true_v.t), key=lambda x: _RANK[x])
            else:
                raise FormulaError(f"both sides must have the same type — got {type_word(true_v.t)} "
                                   f"and {type_word(false_v.t)}")
        fv, tv = self.coerce(false_v, t, "the 'false' value"), self.coerce(true_v, t, "the 'true' value")
        if cond.is_const:
            return tv if cond.c else fv
        if fv.is_const and tv.is_const and fv.c == tv.c:
            return tv
        if not fv.is_const and not tv.is_const and fv.o.key() == tv.o.key():
            return tv
        if t in RESOURCE_TYPES:
            raise FormulaError(f"can't choose between {type_word(t)}s with a condition")
        n = self.g.add("GeometryNodeSwitch", {"input_type": SOCKET_DTYPE[elem_type(t)]},
                       {"Switch": cond.o, "False": self.inp(fv, t), "True": self.inp(tv, t)})
        static_len = fv.c if (is_list(t) and fv.c == tv.c) else None
        return Val(t, c=static_len, o=n.out("Output"), field=cond.field or fv.field or tv.field)


def reference_by_category(target=None):
    """{category: [FuncDef, ...]} with aliases collapsed, for docs and prompts.
    With a target, functions it can't run are left out."""
    if target is not None and not isinstance(target, caps.Target):
        target = caps.Target(target)
    seen, out = set(), {c: [] for c in CATEGORY_ORDER}
    for fd in FUNCS.values():
        if target is not None and fd.feature and not target.supports(fd.feature):
            continue
        key = (fd.sig, fd.doc, fd.category, fd.handler)
        if fd.handler in ("f_poly", "f_vec") and fd.sig.startswith(fd.name + "("):
            key = (fd.name,)
        if key in seen:
            continue
        seen.add(key)
        out.setdefault(fd.category, []).append(fd)
    return out
