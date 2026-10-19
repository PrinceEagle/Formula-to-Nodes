# SPDX-License-Identifier: GPL-3.0-or-later
"""Renders a thumbnail of every recipe (background Blender 5.3, EEVEE):

    blender -b --factory-startup -P promo/recipe_gallery.py -- [key ...]

Writes promo/out/gallery/<key>.png and gallery.json (title, category, level, nodes, seconds)."""

import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

from formula_to_nodes import build, caps, compiler, recipes  # noqa: E402

OUT = os.path.join(HERE, "out", "gallery")
SIZE = 480

# a better showcase than the test setup: base object and parameter values per recipe
OVERRIDES = {
    "twist": {"kind": "column"}, "bend": {"kind": "column"}, "taper": {"kind": "column"},
    "spherize": {"kind": "cube"}, "melt": {"kind": "column"}, "smooth": {"kind": "cube"},
    "ripple": {"kind": "grid"}, "noise_displace": {"kind": "ico"}, "jitter": {"kind": "cube"},
    "attrib_noise": {"params": {"mode": 1}},
    "mountain": {"kind": "grid", "params": {"height": 0.8}},
    "peak": {"kind": "cube", "params": {"distance": 0.15}},
    "group_expression": {"kind": "ico", "params": {"mode": 2, "threshold": 0.55, "delete_group": True}},
    "poly_extrude": {"kind": "grid", "params": {"percent": 0.35, "distance": 0.3, "individual": 1}},
    "connectivity": {"kind": "pieces", "params": {"explode": 0.4}},
    "island_colors": {"kind": "pieces"}, "explode": {"kind": "pieces"},
    "measure": {"kind": "bumpy"},
    "sweep_profile": {"inputs": {"Profile": "circle"}},
    "age_color": {"kind": "plane", "before": "pop_source"},
    "smooth_union": {"kind": "ico"}, "edge_wear": {"kind": "bumpy"}, "curvature_colors": {"kind": "bumpy"},
}


def fresh():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE" if "BLENDER_EEVEE" in {e.identifier for e in
                                                              bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items} \
        else "BLENDER_EEVEE_NEXT"
    sc.render.resolution_x = sc.render.resolution_y = SIZE
    sc.render.film_transparent = False
    try:
        sc.eevee.taa_render_samples = 24
    except AttributeError:
        pass
    sc.view_settings.view_transform = "AgX"
    w = bpy.data.worlds.new("w")
    sc.world = w
    w.use_nodes = True
    bg = w.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.045, 0.047, 0.055, 1)
    bg.inputs["Strength"].default_value = 1.0
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = 3.5
    sun.data.angle = math.radians(8)
    sun.rotation_euler = (math.radians(50), math.radians(10), math.radians(35))
    sc.collection.objects.link(sun)
    fill = bpy.data.objects.new("fill", bpy.data.lights.new("fill", "SUN"))
    fill.data.energy = 0.8
    fill.data.color = (0.6, 0.75, 1.0)
    fill.rotation_euler = (math.radians(60), 0, math.radians(-140))
    sc.collection.objects.link(fill)
    sc.frame_start, sc.frame_end = 1, 250
    return sc


def mesh(kind, name="Object"):
    import bmesh
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    if kind == "ico":
        bmesh.ops.create_icosphere(bm, subdivisions=4, radius=1.0)
    elif kind == "grid":
        bmesh.ops.create_grid(bm, x_segments=60, y_segments=60, size=2.0)
    elif kind == "plane":
        bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=1.5)
    elif kind == "cube":
        bmesh.ops.create_cube(bm, size=1.6)
        bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=6, use_grid_fill=True)
    elif kind == "column":
        bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=10, use_grid_fill=True)
        bmesh.ops.scale(bm, vec=(0.7, 0.7, 3.0), verts=bm.verts)
    elif kind == "bumpy":
        bmesh.ops.create_icosphere(bm, subdivisions=5, radius=1.0)
        for v in bm.verts:
            v.co *= 1 + 0.12 * math.sin(v.co.x * 6) * math.sin(v.co.y * 5) * math.sin(v.co.z * 7)
    elif kind == "pieces":
        for i in range(3):
            for j in range(3):
                r = bmesh.ops.create_cube(bm, size=0.55)
                bmesh.ops.translate(bm, vec=((i - 1) * 0.7, (j - 1) * 0.7, 0), verts=r["verts"])
    elif kind == "line":
        vs = [bm.verts.new((i * 0.25 - 2.4, math.sin(i * 0.45) * 0.6, 0.3 + 0.2 * math.cos(i * 0.3)))
              for i in range(20)]
        for a, b in zip(vs, vs[1:]):
            bm.edges.new((a, b))
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def helper(kind):
    if kind == "cube":
        import bmesh
        me = bpy.data.meshes.new("Helper")
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=0.22)
        bm.to_mesh(me)
        bm.free()
        ob = bpy.data.objects.new("Helper", me)
        bpy.context.scene.collection.objects.link(ob)
        ob.hide_render = True             # instancing still uses its geometry
        return ob
    if kind == "circle":
        cu = bpy.data.curves.new("Profile", "CURVE")
        cu.dimensions = "2D"
        sp = cu.splines.new("POLY")
        n = 12
        sp.points.add(n - 1)
        for i, p in enumerate(sp.points):
            a = i / n * 2 * math.pi
            p.co = (math.cos(a) * 0.18, math.sin(a) * 0.06 + 0.04 * math.sin(3 * a), 0, 1)
        sp.use_cyclic_u = True
        ob = bpy.data.objects.new("Profile", cu)
        bpy.context.scene.collection.objects.link(ob)
        ob.hide_render = True
        return ob
    if kind == "collection":
        coll = bpy.data.collections.new("Rocks")
        bpy.context.scene.collection.children.link(coll)
        for i in range(3):
            ob = mesh("ico" if i % 2 else "cube", f"Rock{i}")
            ob.scale = (0.08 + 0.03 * i,) * 3
            bpy.context.scene.collection.objects.unlink(ob)
            coll.objects.link(ob)
            ob.location = (0, 0, -30)
        return coll
    raise ValueError(kind)


def material(attrs, volume):
    m = bpy.data.materials.new("look")
    m.use_nodes = True
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    if volume:
        pv = nt.nodes.new("ShaderNodeVolumePrincipled")
        pv.inputs["Density"].default_value = 18.0
        pv.inputs["Color"].default_value = (0.85, 0.86, 0.9, 1)
        if any(a in attrs for a in ("temperature", "heat", "flame")):
            pv.inputs["Blackbody Intensity"].default_value = 1.0
            at = nt.nodes.new("ShaderNodeAttribute")
            at.attribute_type = "GEOMETRY"
            at.attribute_name = next(a for a in ("temperature", "heat", "flame") if a in attrs)
            mul = nt.nodes.new("ShaderNodeMath")
            mul.operation = "MULTIPLY"
            mul.inputs[1].default_value = 1500
            nt.links.new(at.outputs["Fac"], mul.inputs[0])
            nt.links.new(mul.outputs[0], pv.inputs["Temperature"])
        nt.links.new(pv.outputs["Volume"], out.inputs["Volume"])
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = 0.45
    bsdf.inputs["Base Color"].default_value = (0.72, 0.72, 0.74, 1)
    shown = None
    if "Cd" in attrs:
        at = nt.nodes.new("ShaderNodeAttribute")
        at.attribute_name = "Cd"
        nt.links.new(at.outputs["Color"], bsdf.inputs["Base Color"])
        shown = "Cd"
    else:
        floats = [a for a, info in attrs.items() if info[0] in ("FLOAT", "BOOL", "INT")
                  and a not in ("radius", "birth", "life", "age", "seed")]
        if floats:
            at = nt.nodes.new("ShaderNodeAttribute")
            at.attribute_name = floats[0]
            ramp = nt.nodes.new("ShaderNodeValToRGB")
            cr = ramp.color_ramp
            cr.elements[0].color = (0.06, 0.04, 0.16, 1)
            cr.elements[1].color = (1.0, 0.85, 0.3, 1)
            mid = cr.elements.new(0.5)
            mid.color = (0.85, 0.25, 0.35, 1)
            nt.links.new(at.outputs["Fac"], ramp.inputs["Fac"])
            nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
            shown = floats[0]
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return m, shown


def set_material_modifier(ob, mat):
    g = bpy.data.node_groups.new("SetMat", "GeometryNodeTree")
    g.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    g.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gi, go = g.nodes.new("NodeGroupInput"), g.nodes.new("NodeGroupOutput")
    rz = g.nodes.new("GeometryNodeRealizeInstances")
    sm = g.nodes.new("GeometryNodeSetMaterial")
    sm.inputs["Material"].default_value = mat
    # curves get a thin tube so they show up in the render
    c2m = g.nodes.new("GeometryNodeCurveToMesh")
    circ = g.nodes.new("GeometryNodeCurvePrimitiveCircle")
    circ.inputs["Radius"].default_value = 0.035
    circ.inputs["Resolution"].default_value = 10
    join = g.nodes.new("GeometryNodeJoinGeometry")
    g.links.new(gi.outputs[0], rz.inputs[0])
    g.links.new(rz.outputs[0], c2m.inputs["Curve"])
    g.links.new(circ.outputs["Curve"], c2m.inputs["Profile Curve"])
    g.links.new(rz.outputs[0], join.inputs[0])
    g.links.new(c2m.outputs[0], join.inputs[0])
    g.links.new(join.outputs[0], sm.inputs[0])
    g.links.new(sm.outputs[0], go.inputs[0])
    ob.modifiers.new("SetMat", "NODES").node_group = g


TIGHT = {}


def frame_camera(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    pts = [ev.matrix_world @ Vector(c) for c in ev.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    if (hi - lo).length < 1e-6 or (hi - lo).length > 1e4:
        lo, hi = Vector((-1, -1, -1)), Vector((1, 1, 1))
    center = (lo + hi) / 2
    radius = max((hi - lo).length / 2, 0.3)
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    cam.data.lens = 50
    bpy.context.scene.collection.objects.link(cam)
    d = Vector((1.0, -1.35, 0.85)).normalized()
    dist = radius / math.sin(math.radians(18)) * TIGHT.get("tight", 1.0)
    cam.location = center + d * dist
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam.data.clip_end = dist * 10
    bpy.context.scene.camera = cam


def render_recipe(r):
    sc = fresh()
    spec = r.test
    ov = OVERRIDES.get(r.key, {})
    kind = ov.get("kind", spec.get("kind", "ico"))
    if kind == "line" and spec.get("kind") != "line":
        kind = spec.get("kind", "ico")
    ob = mesh(kind)
    values = {name: helper(k) for name, k in dict(spec.get("inputs", {}), **ov.get("inputs", {})).items()}
    src = (spec["prelude"] + "\n" + r.script) if spec.get("prelude") else r.script
    if ov.get("before"):           # e.g. a sim that makes the attributes this recipe reads
        pre = recipes.BY_KEY[ov["before"]].script
        res0 = compiler.compile_source(pre, target=caps.from_blender(bpy))
        ob.modifiers.new("before", "NODES").node_group = build.build_group(res0, pre, "SCRIPT", name="before")
    res = compiler.compile_source(src, target=caps.from_blender(bpy))
    tree = build.build_group(res, src, "SCRIPT", name=r.key)
    mod = ob.modifiers.new(r.key, "NODES")
    mod.node_group = tree
    for name, value in values.items():
        it = next((i for i in tree.interface.items_tree if i.item_type == "SOCKET" and i.in_out == "INPUT"
                   and i.name == name), None)
        if it is not None:
            getattr(mod.properties.inputs, it.identifier).value = value
    for name, value in ov.get("params", {}).items():
        it = next((i for i in tree.interface.items_tree if i.item_type == "SOCKET" and i.in_out == "INPUT"
                   and i.name == name), None)
        if it is not None:
            try:
                getattr(mod.properties.inputs, it.identifier).value = value
            except (TypeError, ValueError):
                pass
    volume = r.category == "Smoke & Fire" or "keep(\"volume\")" in r.script
    mat, shown = material(res.attrs, volume)
    ob.data.materials.append(mat)
    set_material_modifier(ob, mat)
    text = r.script
    frames = 36 if ("simulate" in text or "@Time" in text or "@Frame" in text) else 1
    frames = max(frames, spec.get("frames", 1), 36 if ov.get("before") else 1)
    sc.frame_set(1)
    for f in range(2, frames + 1):
        sc.frame_set(f)
    TIGHT["tight"] = 0.8 if volume else 1.0
    frame_camera(ob)
    sc.render.filepath = os.path.join(OUT, r.key + ".png")
    bpy.ops.render.render(write_still=True)
    return {"nodes": res.node_count, "shown": shown, "frames": frames}


def main():
    os.makedirs(OUT, exist_ok=True)
    keys = [a for a in (sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []) if a != "--"]
    todo = [recipes.BY_KEY[k] for k in keys] if keys else list(recipes.RECIPES)
    meta_path = os.path.join(OUT, "gallery.json")
    meta = json.load(open(meta_path)) if os.path.exists(meta_path) else {}
    for r in todo:
        if tuple(bpy.app.version[:3]) < r.blender:
            continue
        t = time.time()
        try:
            info = render_recipe(r)
            info["ok"] = True
        except Exception as e:
            info = {"ok": False, "error": str(e)[:300]}
        info.update(title=r.title, category=r.category, level=recipes.level(r),
                    seconds=round(time.time() - t, 1), color=recipes.category_hex(r.category))
        meta[r.key] = info
        json.dump(meta, open(meta_path, "w"), indent=1)
        print("GALLERY", r.key, json.dumps(info), flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
