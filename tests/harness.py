# SPDX-License-Identifier: GPL-3.0-or-later
"""Test helpers that build scripts into real Geometry Nodes trees and read
the evaluated geometry back. Needs Blender's Python module (pip install bpy).
"""

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bpy  # noqa: E402

import formula_to_nodes  # noqa: E402
from formula_to_nodes import build, caps, compiler  # noqa: E402

_registered = False


def ensure_registered():
    global _registered
    if not _registered:
        formula_to_nodes.register()
        _registered = True


def target():
    return caps.from_blender(bpy)


def clear():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    for ng in list(bpy.data.node_groups):
        bpy.data.node_groups.remove(ng)
    for me in list(bpy.data.meshes):
        bpy.data.meshes.remove(me)


def make_object(kind="ico", **kw):
    """A fresh mesh object: ico (subdiv 1: 12 points), grid (4x4 verts), cube, plane, line."""
    import bmesh
    me = bpy.data.meshes.new(kind)
    bm = bmesh.new()
    if kind == "ico":
        bmesh.ops.create_icosphere(bm, subdivisions=kw.get("subdivisions", 1), radius=kw.get("radius", 1.0))
    elif kind == "grid":
        bmesh.ops.create_grid(bm, x_segments=kw.get("x", 3), y_segments=kw.get("y", 3), size=kw.get("size", 1.0))
    elif kind == "cube":
        bmesh.ops.create_cube(bm, size=kw.get("size", 2.0))
    elif kind == "plane":
        bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=kw.get("size", 1.0))
    elif kind == "line":
        n = kw.get("count", 5)
        verts = [bm.verts.new((i, 0, 0)) for i in range(n)]
        for a, b in zip(verts, verts[1:]):
            bm.edges.new((a, b))
    else:
        raise ValueError(kind)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(kind, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def set_input(mod, tree, name, value):
    """Set a modifier input by its interface name (Blender 5.2 API)."""
    ident = next(it.identifier for it in tree.interface.items_tree
                 if it.item_type == "SOCKET" and it.in_out == "INPUT" and it.name == name)
    getattr(mod.properties.inputs, ident).value = value


def get_input(mod, tree, name):
    ident = next(it.identifier for it in tree.interface.items_tree
                 if it.item_type == "SOCKET" and it.in_out == "INPUT" and it.name == name)
    return getattr(mod.properties.inputs, ident).value


class Result:
    def __init__(self, res, tree, ob, mod, geo, warnings):
        self.res, self.tree, self.ob, self.mod, self.geo, self.warnings = res, tree, ob, mod, geo, warnings

    def comp(self, name):
        if name == "instances":
            return self.geo.instances_pointcloud()
        return getattr(self.geo, name)

    def count(self, comp="mesh", domain="POINT"):
        c = self.comp(comp)
        if c is None:
            return 0
        if comp == "mesh":
            return {"POINT": len(c.vertices), "EDGE": len(c.edges), "FACE": len(c.polygons),
                    "CORNER": len(c.loops)}[domain]
        if comp in ("pointcloud", "instances"):
            return len(c.points)
        if comp == "curves":
            return len(c.points) if domain == "POINT" else len(c.curves)
        raise ValueError(comp)

    def attr(self, name, comp="mesh"):
        c = self.comp(comp)
        if c is None:
            raise KeyError(f"no {comp} component")
        a = c.attributes.get(name)
        if a is None:
            raise KeyError(f"attribute '{name}' not on {comp} (has {[x.name for x in c.attributes]})")
        out = []
        for d in a.data:
            for k in ("vector", "color", "value", "string"):
                if hasattr(d, k):
                    v = getattr(d, k)
                    if isinstance(v, (int, float, bool, str)):
                        out.append(v)
                    else:
                        try:
                            out.append(tuple(v))
                        except TypeError:
                            out.append(v)
                    break
        return out

    def domain_of(self, name, comp="mesh"):
        return self.comp(comp).attributes[name].domain

    def has(self, name, comp="mesh"):
        c = self.comp(comp)
        return c is not None and c.attributes.get(name) is not None

    def positions(self, comp="mesh"):
        return self.attr("position", comp)


def run(script, ob=None, runover=None, params=None, kind="ico", setup=None, frame=None, **kw):
    """Compile + build + evaluate. Returns a Result."""
    ensure_registered()
    res = compiler.compile_source(script, runover=runover, target=target())
    tree = build.build_group(res, script, "SCRIPT", name=kw.get("name", "Test"))
    if ob is None:
        ob = make_object(kind, **{k: v for k, v in kw.items() if k != "name"})
    mod = ob.modifiers.new("ftn", "NODES")
    mod.node_group = tree
    if params:
        for k, v in params.items():
            set_input(mod, tree, k, v)
    if setup:
        setup(ob, mod, tree)
    if frame is not None:
        bpy.context.scene.frame_set(frame)
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    ev = ob.evaluated_get(dg)
    geo = ev.evaluated_geometry()
    warnings = [f"{w.type}:{w.message}" for w in getattr(mod, "node_warnings", [])]
    return Result(res, tree, ob, mod, geo, warnings)


def close(a, b, tol=1e-4):
    if isinstance(a, (tuple, list)):
        return len(a) == len(b) and all(close(x, y, tol) for x, y in zip(a, b))
    return abs(a - b) <= tol
