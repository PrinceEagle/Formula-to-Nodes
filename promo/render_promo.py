# SPDX-License-Identifier: GPL-3.0-or-later
"""Renders the promo images: real recipe scripts built into Geometry Nodes and
rendered with Cycles. Run with Blender's Python module:
    python promo/render_promo.py   (needs `pip install bpy`)
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, os.path.join(HERE, ".."))

import bpy  # noqa: E402
import harness as H  # noqa: E402
from formula_to_nodes import build, compiler, ramps  # noqa: E402

PALETTE = next((p for p in ("magma", "viridis", "sunset", "terrain") if p in ramps.COLOR_PRESETS), "grayscale")

SHOTS = [
    ("voronoi", "ico", {"subdivisions": 5},
     ['#runover prim\n@Cd = hsvtorgb(set(cellrand(v@P, chf("cells", 4)), 0.7, 0.85));'],
     (0.0, -4.9, 1.5), (1.29, 0.0, 0.0)),
    ("greebles", "grid", {"x": 28, "y": 28, "size": 1.6},
     ['#runover prim\nif (rand(@primnum + chi("seed", 0)) < chf("amount", 0.35)) {\n'
      '    extrude(fit01(rand(@primnum + 17), chf("Height/min", 0.05), chf("Height/max", 0.4)), 1);\n}',
      '@Cd = colorramp("palette", relbbox(0, v@P).z, "terrain");'],
     (3.3, -3.3, 3.0), (0.95, 0.0, 0.785)),
    ("displace", "ico", {"subdivisions": 6},
     ['float n = snoise(v@P, chf("scale", 2.5));\n@P += v@N * n * chf("strength", 0.25);\nf@displacement = n;\n'
      'shadesmooth(1);',
      f'@Cd = colorramp("heat", fit(f@displacement, -0.7, 0.7, 0, 1), "{PALETTE}");'],
     (0.0, -5.0, 1.3), (1.32, 0.0, 0.0)),
]


def setup_scene():
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = 48
    try:
        sc.cycles.use_denoising = True
    except Exception:
        pass
    sc.render.resolution_x, sc.render.resolution_y = 1280, 960
    sc.render.film_transparent = False
    sc.view_settings.view_transform = "Standard"
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.035, 0.038, 0.05, 1)
    bg.inputs["Strength"].default_value = 1.0
    sc.world = world
    sun = bpy.data.lights.new("sun", "SUN")
    sun.energy = 3.5
    sun.angle = 0.12
    so = bpy.data.objects.new("sun", sun)
    so.rotation_euler = (0.75, 0.15, 0.6)
    sc.collection.objects.link(so)
    fill = bpy.data.lights.new("fill", "AREA")
    fill.energy = 250
    fill.size = 4
    fo = bpy.data.objects.new("fill", fill)
    fo.location = (-3, -2, 3)
    fo.rotation_euler = (0.9, 0, -0.9)
    sc.collection.objects.link(fo)
    cam = bpy.data.cameras.new("cam")
    cam.lens = 42
    co = bpy.data.objects.new("cam", cam)
    sc.collection.objects.link(co)
    sc.camera = co
    mat = bpy.data.materials.new("cd")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.42
    attr = nt.nodes.new("ShaderNodeAttribute")
    attr.attribute_name = "Cd"
    nt.links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
    return sc, co, mat


def main():
    H.ensure_registered()
    out = {}
    for key, kind, kw, scripts, cam_loc, cam_rot in SHOTS:
        for ob in list(bpy.data.objects):
            bpy.data.objects.remove(ob)
        for ng in list(bpy.data.node_groups):
            bpy.data.node_groups.remove(ng)
        sc, cam, mat = setup_scene()
        ob = H.make_object(kind, **kw)
        ob.data.materials.append(mat)
        for i, src in enumerate(scripts):
            res = compiler.compile_source(src, target=H.target())
            tree = build.build_group(res, src, "SCRIPT", name=f"{key}{i}")
            mod = ob.modifiers.new(f"Formula {i}", "NODES")
            mod.node_group = tree
        # material for geometry the nodes create
        sm = ob.modifiers.new("mat", "NODES")
        g = bpy.data.node_groups.new("setmat", "GeometryNodeTree")
        g.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        g.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        gi, go = g.nodes.new("NodeGroupInput"), g.nodes.new("NodeGroupOutput")
        smn = g.nodes.new("GeometryNodeSetMaterial")
        smn.inputs["Material"].default_value = mat
        g.links.new(gi.outputs[0], smn.inputs["Geometry"])
        g.links.new(smn.outputs["Geometry"], go.inputs[0])
        sm.node_group = g
        cam.location, cam.rotation_euler = cam_loc, cam_rot
        path = os.path.join(HERE, f"render_{key}.png")
        sc.render.filepath = path
        bpy.ops.render.render(write_still=True)
        out[key] = {"image": os.path.basename(path), "scripts": scripts}
        print("rendered", path, flush=True)
    with open(os.path.join(HERE, "shots.json"), "w") as f:
        json.dump(out, f, indent=1)


main()
