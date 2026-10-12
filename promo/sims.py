# SPDX-License-Identifier: GPL-3.0-or-later
"""Promo simulations: builds a recipe into a fresh scene, simulates it,
measures playback speed and renders stills (and optionally an image
sequence) with Cycles. Runs in a background Blender, never in your file:

    blender -b --factory-startup -P promo/sims.py -- --shot smoke_plume --frames 140 --stills 60,100,140

Writes promo/out/<shot>_fNNNN.png and promo/out/<shot>.json (timings, node
count, voxel count, script).
"""

import argparse
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

from formula_to_nodes import build, caps, compiler, recipes  # noqa: E402

OUT = os.path.join(HERE, "out")


# ─────────────────────────────────────────────────────────────────────────────
#  scene helpers
# ─────────────────────────────────────────────────────────────────────────────

LIVE_SCENE = None      # set by live_promo.py: build into a new scene of the open file instead


def reset():
    if LIVE_SCENE:
        old = bpy.data.scenes.get(LIVE_SCENE)
        if old is not None:
            for ob in list(old.collection.all_objects):
                if len(ob.users_scene) == 1:
                    bpy.data.objects.remove(ob)
            bpy.data.scenes.remove(old)
        sc = bpy.data.scenes.new(LIVE_SCENE)
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for kind in ("METAL", "OPTIX", "CUDA", "HIP", "ONEAPI"):
        try:
            prefs.compute_device_type = kind
            prefs.get_devices()
            if any(d.type == kind for d in prefs.devices):
                for d in prefs.devices:
                    d.use = d.type == kind
                sc.cycles.device = "GPU"
                break
        except TypeError:
            continue
    sc.cycles.use_denoising = True
    sc.cycles.max_bounces = 6
    sc.cycles.volume_bounces = 1
    sc.cycles.transparent_max_bounces = 16
    sc.cycles.volume_step_rate = 1.0
    print("DEVICE", sc.cycles.device, [(d.name, d.type, d.use) for d in prefs.devices], flush=True)
    sc.view_settings.view_transform = "AgX"
    sc.view_settings.look = "AgX - Medium High Contrast"
    sc.render.resolution_x, sc.render.resolution_y = 1080, 1350
    sc.frame_start, sc.frame_end = 1, 250
    sc.render.fps = 24
    return sc


def world(sc, color=(0.012, 0.013, 0.018), strength=1.0):
    w = bpy.data.worlds.new(f"{sc.name} World")
    w.use_nodes = True
    bg = w.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (*color, 1)
    bg.inputs["Strength"].default_value = strength
    sc.world = w


def look_at(ob, target):
    d = Vector(target) - ob.location
    ob.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()


def camera(sc, loc, target, lens=50):
    cam = bpy.data.cameras.new("Camera")
    cam.lens = lens
    ob = bpy.data.objects.new("Camera", cam)
    sc.collection.objects.link(ob)
    ob.location = loc
    look_at(ob, target)
    sc.camera = ob
    return ob


def light(sc, kind, loc, target, energy, color=(1, 1, 1), size=1.0, name=None):
    data = bpy.data.lights.new(name or kind.title(), kind)
    data.energy = energy
    data.color = color
    if kind == "AREA":
        data.size = size
    elif kind == "SPOT":
        data.spot_size = math.radians(45)
        data.spot_blend = 0.6
        data.shadow_soft_size = size
    elif kind == "SUN":
        data.angle = size
    ob = bpy.data.objects.new(data.name, data)
    sc.collection.objects.link(ob)
    ob.location = loc
    look_at(ob, target)
    return ob


def floor(sc, size=40, color=(0.02, 0.02, 0.024), rough=0.35, z=0.0):
    me = bpy.data.meshes.new("Floor")
    s = size / 2
    me.from_pydata([(-s, -s, z), (s, -s, z), (s, s, z), (-s, s, z)], [], [(0, 1, 2, 3)])
    ob = bpy.data.objects.new("Floor", me)
    sc.collection.objects.link(ob)
    m = bpy.data.materials.new("Floor")
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Roughness"].default_value = rough
    me.materials.append(m)
    return ob


def volume_material(name, color=(0.85, 0.85, 0.88), density=8.0, anisotropy=0.35, fire=0.0, fire_color=None):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    pv = nt.nodes.new("ShaderNodeVolumePrincipled")
    pv.inputs["Color"].default_value = (*color, 1)
    pv.inputs["Density"].default_value = density
    pv.inputs["Anisotropy"].default_value = anisotropy
    pv.inputs["Density Attribute"].default_value = "density"
    if fire:
        pv.inputs["Temperature Attribute"].default_value = "temperature"
        pv.inputs["Blackbody Intensity"].default_value = fire
        if fire_color:
            pv.inputs["Blackbody Tint"].default_value = (*fire_color, 1)
    nt.links.new(pv.outputs["Volume"], out.inputs["Volume"])
    return m


def surface_material(name, color, rough=0.4, metal=0.0, transmission=0.0, ior=1.45, emission=None):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    b.inputs["Transmission Weight"].default_value = transmission
    b.inputs["IOR"].default_value = ior
    if emission:
        b.inputs["Emission Color"].default_value = (*emission[0], 1)
        b.inputs["Emission Strength"].default_value = emission[1]
    return m


def mesh_object(sc, kind, name, **kw):
    import bmesh
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    if kind == "plane":
        bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=kw.get("size", 1.0))
    elif kind == "grid":
        bmesh.ops.create_grid(bm, x_segments=kw.get("x", 10), y_segments=kw.get("y", 10), size=kw.get("size", 1.0))
    elif kind == "ico":
        bmesh.ops.create_icosphere(bm, subdivisions=kw.get("subdivisions", 2), radius=kw.get("radius", 1.0))
    elif kind == "cube":
        bmesh.ops.create_cube(bm, size=kw.get("size", 1.0))
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    sc.collection.objects.link(ob)
    return ob


def material_modifier(ob):
    """Geometry the nodes create (grid(), points(), volumes...) has no material: give it the object's."""
    if not getattr(ob.data, "materials", None) or not ob.data.materials[0]:
        return
    name = "FTN Set Material"
    g = bpy.data.node_groups.get(name)
    if g is None:
        g = bpy.data.node_groups.new(name, "GeometryNodeTree")
        g.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        g.interface.new_socket("Material", in_out="INPUT", socket_type="NodeSocketMaterial")
        g.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        gi, go = g.nodes.new("NodeGroupInput"), g.nodes.new("NodeGroupOutput")
        sm = g.nodes.new("GeometryNodeSetMaterial")
        g.links.new(gi.outputs["Geometry"], sm.inputs["Geometry"])
        g.links.new(gi.outputs["Material"], sm.inputs["Material"])
        g.links.new(sm.outputs["Geometry"], go.inputs[0])
        gi.location, sm.location, go.location = (-300, 0), (0, 0), (250, 0)
    mod = ob.modifiers.new("Material", "NODES")
    mod.node_group = g
    ident = next(it.identifier for it in g.interface.items_tree
                 if it.item_type == "SOCKET" and it.in_out == "INPUT" and it.name == "Material")
    getattr(mod.properties.inputs, ident).value = ob.data.materials[0]


def add_recipe(ob, key, params=None, script=None, name=None):
    src = script or recipes.get(key).script
    res = compiler.compile_source(src, target=caps.from_blender(bpy))
    tree = build.build_group(res, src, "SCRIPT", name=name or key)
    mod = ob.modifiers.new(name or key, "NODES")
    mod.node_group = tree
    for k, v in (params or {}).items():
        panel, _, nm = k.rpartition("/")
        ident = next(it.identifier for it in tree.interface.items_tree
                     if it.item_type == "SOCKET" and it.in_out == "INPUT" and it.name == nm
                     and (not panel or (it.parent is not None and it.parent.name == panel)))
        getattr(mod.properties.inputs, ident).value = v
    material_modifier(ob)
    return mod, res, src


def simulate(sc, frames, probe=None):
    """Steps the scene from frame_start, returns per-frame milliseconds."""
    sc.frame_set(sc.frame_start)
    times = []
    for f in range(sc.frame_start + 1, sc.frame_start + frames + 1):
        t = time.perf_counter()
        sc.frame_set(f)
        bpy.context.evaluated_depsgraph_get()
        times.append((time.perf_counter() - t) * 1000)
        if probe:
            probe(f)
    return times


def render(sc, path, samples=128):
    sc.cycles.samples = samples
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)


# ─────────────────────────────────────────────────────────────────────────────
#  shots
# ─────────────────────────────────────────────────────────────────────────────

def shot_smoke_plume(args):
    sc = reset()
    world(sc, (0.004, 0.005, 0.007))
    ob = mesh_object(sc, "plane", "Smoke", size=0.1)
    ob.data.materials.append(volume_material("Smoke", (0.62, 0.63, 0.66), density=args.density, anisotropy=0.3))
    params = {"min": (-1.3, -1.3, 0.0), "max": (1.3, 1.3, 5.0)}
    if args.voxel:
        params["voxel_size"] = args.voxel
    mod, res, src = add_recipe(ob, "smoke_plume", params)
    camera(sc, (0, -8.8, 1.9), (0, 0, 2.1), lens=45)
    light(sc, "AREA", (4.5, 0.5, 3.0), (0, 0, 2.2), 2600, (1.0, 0.82, 0.62), size=1.2)      # warm side key
    light(sc, "AREA", (-2.5, 4.0, 3.5), (0, 0, 2.2), 3500, (0.5, 0.68, 1.0), size=2.0)     # cool rim
    light(sc, "AREA", (-4, -3, 1.5), (0, 0, 2.2), 30, (0.7, 0.75, 0.9), size=4)           # fill
    return sc, ob, res, src


def shot_fire(args):
    sc = reset()
    world(sc, (0.004, 0.004, 0.006))
    floor(sc, color=(0.03, 0.025, 0.022), rough=0.7)
    ob = mesh_object(sc, "plane", "Fire", size=0.1)
    m = volume_material("Fire", (0.06, 0.055, 0.05), density=args.density, anisotropy=0.2, fire=6.0)
    pv = next(n for n in m.node_tree.nodes if n.bl_idname == "ShaderNodeVolumePrincipled")
    pv.inputs["Temperature"].default_value = 1400
    ob.data.materials.append(m)
    params = {"radius": 0.42, "strength": 5.0}
    if args.voxel:
        params["voxel_size"] = args.voxel
    mod, res, src = add_recipe(ob, "fire", params)
    camera(sc, (0, -4.6, 1.15), (0, 0, 1.05), lens=50)
    light(sc, "AREA", (-3, 2.5, 3), (0, 0, 1), 150, (0.5, 0.65, 1.0), size=3)
    return sc, ob, res, src


def shot_smoke_ring(args):
    sc = reset()
    world(sc, (0.005, 0.006, 0.009))
    ob = mesh_object(sc, "plane", "Ring", size=0.1)
    ob.data.materials.append(volume_material("Ring", (0.95, 0.95, 0.97), density=args.density, anisotropy=0.5))
    params = {"voxel_size": args.voxel} if args.voxel else {}
    mod, res, src = add_recipe(ob, "smoke_ring", params)
    camera(sc, (0, -6.0, 0.2), (0, 0, 1.8), lens=50)
    light(sc, "AREA", (3.5, -2.0, 4.5), (0, 0, 2), 1400, (1.0, 0.85, 0.7), size=1.5)
    light(sc, "AREA", (-2.5, 4.0, 3.5), (0, 0, 2.2), 2500, (0.45, 0.65, 1.0), size=2.0)
    return sc, ob, res, src


def shot_bubbles(args):
    sc = reset()
    world(sc, (0.002, 0.01, 0.03))
    ob = mesh_object(sc, "plane", "Bubbles", size=0.1)
    ob.data.materials.append(surface_material("Bubble", (1, 1, 1), rough=0.0, transmission=1.0, ior=0.75))
    params = {"count": 900}
    mod, res, src = add_recipe(ob, "bubbles", params)
    # a glowing back panel gives the bubbles bright rims, like a lit aquarium
    back = mesh_object(sc, "plane", "Backlight", size=6)
    back.location, back.rotation_euler = (0, 2.2, 1.0), (math.radians(90), 0, 0)
    back.data.materials.append(surface_material("Glow", (0, 0, 0), emission=((0.05, 0.3, 0.75), 0.55)))
    camera(sc, (0, -3.3, 1.0), (0, 0, 1.0), lens=50)
    light(sc, "AREA", (0, -1, 3.2), (0, 0, 1), 250, (0.8, 0.9, 1.0), size=2)
    return sc, ob, res, src


def shot_shallow_water(args):
    sc = reset()
    world(sc, (0.03, 0.035, 0.045))
    ob = mesh_object(sc, "plane", "Water", size=0.1)
    m = surface_material("Water", (0.1, 0.3, 0.4), rough=0.05)
    nt = m.node_tree
    attr = nt.nodes.new("ShaderNodeAttribute")
    attr.attribute_name = "Cd"
    nt.links.new(attr.outputs["Color"], nt.nodes["Principled BSDF"].inputs["Base Color"])
    ob.data.materials.append(m)
    mod, res, src = add_recipe(ob, "shallow_water", {})
    tm = surface_material("Tank", (0.03, 0.03, 0.035), rough=0.5)
    for name, loc, scale in (("Tank floor", (0, 0, -0.03), (2.12, 2.12, 0.06)),
                             ("Tank wall N", (0, 1.04, 0.2), (2.12, 0.04, 0.5)),
                             ("Tank wall W", (-1.04, 0, 0.2), (0.04, 2.12, 0.5))):
        t = mesh_object(sc, "cube", name, size=1.0)
        t.location, t.scale = loc, scale
        t.data.materials.append(tm)
    camera(sc, (2.75, -2.75, 1.55), (-0.05, 0.05, 0.12), lens=40)
    light(sc, "SUN", (0, 0, 5), (0.6, 0.9, 0), 2.2, (1.0, 0.94, 0.85), size=0.03)
    light(sc, "AREA", (-3, -1, 2.5), (0, 0, 0), 500, (0.55, 0.75, 1.0), size=4)
    return sc, ob, res, src


def shot_galaxy(args):
    """Stars (points, surface emission) and dust (volume emission) as two objects running the
    same recipe — one material carrying both a surface and a volume shader renders black in
    Cycles here."""
    sc = reset()
    world(sc, (0.0, 0.0, 0.0))
    stars = mesh_object(sc, "plane", "Galaxy", size=0.1)
    m = bpy.data.materials.new("Stars")
    m.use_nodes = True
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Strength"].default_value = 4.0
    pt = nt.nodes.new("ShaderNodeAttribute")
    pt.attribute_name = "Cd"
    nt.links.new(pt.outputs["Color"], em.inputs["Color"])
    nt.links.new(em.outputs[0], out.inputs["Surface"])
    stars.data.materials.append(m)
    mod, res, src = add_recipe(stars, "galaxy", {})
    dust = mesh_object(sc, "plane", "Galaxy dust", size=0.1)
    d = bpy.data.materials.new("Dust")
    d.use_nodes = True
    nt = d.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    pv = nt.nodes.new("ShaderNodeVolumePrincipled")
    pv.inputs["Density"].default_value = 0.0
    col = nt.nodes.new("ShaderNodeAttribute")
    col.attribute_name = "color"
    den = nt.nodes.new("ShaderNodeAttribute")
    den.attribute_name = "density"
    mul = nt.nodes.new("ShaderNodeMath")
    mul.operation = "MULTIPLY"
    mul.inputs[1].default_value = args.density
    nt.links.new(den.outputs[2], mul.inputs[0])
    nt.links.new(col.outputs["Color"], pv.inputs["Emission Color"])
    nt.links.new(mul.outputs[0], pv.inputs["Emission Strength"])
    nt.links.new(pv.outputs["Volume"], out.inputs["Volume"])
    dust.data.materials.append(d)
    add_recipe(dust, "galaxy", {})
    add_recipe(dust, None, script='keep("volume");', name="dust only")
    camera(sc, (0, -3.0, 2.0), (0, 0, -0.05), lens=40)
    return sc, stars, res, src


def shot_lava_lamp(args):
    sc = reset()
    world(sc, (0.012, 0.002, 0.02))
    floor(sc, color=(0.015, 0.01, 0.015), rough=0.35)
    ob = mesh_object(sc, "plane", "Lava", size=0.1)
    m = surface_material("Wax", (1.0, 0.12, 0.02), rough=0.3, emission=((1.0, 0.18, 0.02), 1.2))
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Subsurface Weight"].default_value = 0.8
    b.inputs["Subsurface Radius"].default_value = (0.4, 0.12, 0.05)
    ob.data.materials.append(m)
    mod, res, src = add_recipe(ob, "lava_lamp", {"blobs": 30, "min": 0.05, "max": 0.12, "melt": 4})
    # the lamp: glass bottle, metal base and cap
    glass = surface_material("Lamp glass", (0.9, 0.6, 0.85), rough=0.02, transmission=1.0, ior=1.45)
    metal = surface_material("Lamp metal", (0.6, 0.6, 0.65), rough=0.25, metal=1.0)
    _cone(sc, "Lamp glass", 0.42, 0.42, 2.15, (0, 0, 1.03), glass)
    _cone(sc, "Lamp base", 0.62, 0.43, 0.45, (0, 0, -0.2), metal)
    _cone(sc, "Lamp cap", 0.43, 0.2, 0.3, (0, 0, 2.25), metal)
    camera(sc, (0, -4.4, 1.2), (0, 0, 1.0), lens=50)
    light(sc, "AREA", (2.5, -2, 3), (0, 0, 1), 400, (1.0, 0.5, 0.8), size=2)
    light(sc, "AREA", (-2.5, 2, 2), (0, 0, 1), 500, (0.35, 0.45, 1.0), size=2)
    return sc, ob, res, src


def shot_cloud(args):
    sc = reset()
    world(sc, (0.12, 0.28, 0.65), strength=1.0)
    sc.cycles.volume_bounces = 12
    sc.cycles.max_bounces = 16
    import bmesh
    me = bpy.data.meshes.new("CloudShape")
    bm = bmesh.new()
    for (x, y, z, r) in ((0, 0, 0.35, 0.55), (0.6, 0.1, 0.25, 0.42), (-0.6, -0.05, 0.22, 0.4),
                         (0.25, -0.2, 0.75, 0.42), (-0.3, 0.15, 0.62, 0.36), (1.05, 0, 0.12, 0.28),
                         (-1.0, 0.1, 0.1, 0.27)):
        geom = bmesh.ops.create_icosphere(bm, subdivisions=3, radius=r)
        bmesh.ops.translate(bm, vec=(x, y, z), verts=geom["verts"])
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("Cloud", me)
    sc.collection.objects.link(ob)
    ob.data.materials.append(volume_material("Cloud", (1, 1, 1), density=args.density, anisotropy=0.6))
    mod, res, src = add_recipe(ob, "cloud", {"voxel_size": args.voxel or 0.02, "amount": 0.3})
    camera(sc, (0, -5.2, 0.6), (0, 0, 0.42), lens=50)
    light(sc, "SUN", (3, -5, 4), (0, 0, 0.4), 7.0, (1.0, 0.93, 0.82), size=0.02)
    return sc, ob, res, src


def _cd_material(name, rough=0.05, spec=0.5):
    m = surface_material(name, (0.1, 0.3, 0.4), rough=rough)
    nt = m.node_tree
    attr = nt.nodes.new("ShaderNodeAttribute")
    attr.attribute_name = "Cd"
    nt.links.new(attr.outputs["Color"], nt.nodes["Principled BSDF"].inputs["Base Color"])
    return m


def shot_ocean_waves(args):
    sc = reset()
    world(sc, (0.35, 0.5, 0.75), strength=0.9)
    ob = mesh_object(sc, "grid", "Ocean", x=420, y=420, size=18)
    ob.data.materials.append(_cd_material("Ocean", rough=0.06))
    mod, res, src = add_recipe(ob, "ocean_waves", {"height": 0.14, "wavelength": 2.4})
    camera(sc, (0, -7.5, 1.35), (0, 2.0, 0.0), lens=35)
    light(sc, "SUN", (0, 5, 3), (0, 0, 0), 4.0, (1.0, 0.85, 0.65), size=0.02)
    return sc, ob, res, src


def shot_whirlpool(args):
    sc = reset()
    world(sc, (0.05, 0.08, 0.12), strength=1.0)
    ob = mesh_object(sc, "grid", "Whirlpool", x=260, y=260, size=4)
    ob.data.materials.append(_cd_material("Whirlpool", rough=0.06))
    mod, res, src = add_recipe(ob, "whirlpool", {})
    camera(sc, (0, -3.4, 2.6), (0, 0, -0.15), lens=38)
    light(sc, "SUN", (2, -2, 4), (0, 0, 0), 3.0, (1.0, 0.95, 0.9), size=0.03)
    light(sc, "AREA", (-2, 2, 2.5), (0, 0, 0), 300, (0.5, 0.7, 1.0), size=3)
    return sc, ob, res, src


def shot_pfm_smoke(args):
    """Zhou et al. 2024 particle flow maps: two smoke rings leapfrogging — the vortex-preservation test."""
    sc = reset()
    world(sc, (0.003, 0.004, 0.007))
    ob = mesh_object(sc, "plane", "PFM Rings", size=0.1)
    ob.data.materials.append(volume_material("PFM Smoke", (0.95, 0.95, 0.97), density=args.density, anisotropy=0.5))
    params = {"min": (-0.9, -0.9, 0.0), "max": (0.9, 0.9, 3.2), "voxel_size": args.voxel or 0.02,
              "buoyancy": 0.0, "radius": 0.0001, "per_voxel": 2, "reinit_frames": 1, "affine_gradient": 1.0,
              "max_stretch": 1.5, "rings": 2, "ring_speed": 7.0, "spacing": 0.32, "ring_center": (0, 0, 0.3), "ring_radius": 0.3,
              "smoke_core": 0.13}
    mod, res, src = add_recipe(ob, "pfm_smoke", params)
    camera(sc, (0, -3.4, 2.2), (0, 0, 1.15), lens=50)
    light(sc, "AREA", (3.5, -2.0, 4.0), (0, 0, 1.6), 1800, (1.0, 0.82, 0.65), size=1.0)
    light(sc, "AREA", (-2.5, 3.5, 3.5), (0, 0, 1.8), 2800, (0.45, 0.65, 1.0), size=2.0)
    light(sc, "AREA", (-4, -3, 0.8), (0, 0, 1.2), 120, (0.7, 0.75, 0.9), size=5)
    return sc, ob, res, src


def shot_weta_bubbles(args):
    """Wretborn, Stomakhin & Batty 2025: a two-way coupled bubble plume in a lit tank."""
    sc = reset()
    world(sc, (0.0, 0.006, 0.016))
    ob = mesh_object(sc, "plane", "Air", size=0.01)
    ob.data.materials.append(surface_material("Air in water", (1, 1, 1), rough=0.0, transmission=1.0,
                                              ior=1.0 / 1.333))
    params = {"voxel_size": args.voxel or 0.0125, "particles": 30000, "bubbles_per_second": 3000,
              "radius_min": 0.0012, "radius_max": 0.009, "substeps": 3, "radius": 0.03,
              "vents": 3, "vent_ring": 0.11, "puff_period": 0.6, "zigzag": 0.55}
    params["merge"] = getattr(args, "merge", None) or 0.15
    mod, res, src = add_recipe(ob, "weta_bubbles", params)
    # water: a tinted absorbing volume filling the tank
    water = mesh_object(sc, "cube", "Water", size=1.0)
    water.scale, water.location = (0.6, 0.6, 1.0), (0, 0, 0.5)
    wm = bpy.data.materials.new("Water")
    wm.use_nodes = True
    nt = wm.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    pv = nt.nodes.new("ShaderNodeVolumePrincipled")
    pv.inputs["Color"].default_value = (0.35, 0.75, 0.95, 1)
    pv.inputs["Density"].default_value = 0.35
    pv.inputs["Absorption Color"].default_value = (0.2, 0.55, 0.75, 1)
    pv.inputs["Anisotropy"].default_value = 0.6
    nt.links.new(pv.outputs["Volume"], out.inputs["Volume"])
    water.data.materials.append(wm)
    # backlight and a "sun through the surface" from above
    back = mesh_object(sc, "plane", "Backlight", size=3)
    back.location, back.rotation_euler = (0, 1.2, 0.6), (math.radians(90), 0, 0)
    back.data.materials.append(surface_material("Glow", (0, 0, 0), emission=((0.03, 0.22, 0.6), 0.22)))
    cam = camera(sc, (0, -1.35, 0.5), (0, 0, 0.52), lens=50)
    cam.data.dof.use_dof = True
    cam.data.dof.focus_distance = 1.35
    cam.data.dof.aperture_fstop = 2.8
    light(sc, "SPOT", (0.15, -0.1, 1.6), (0, 0, 0.4), 420, (0.85, 0.95, 1.0), size=0.05)
    light(sc, "AREA", (-0.9, -0.6, 0.7), (0, 0, 0.5), 40, (0.6, 0.8, 1.0), size=1.0)
    return sc, ob, res, src


SHOTS = {"pfm_smoke": shot_pfm_smoke, "weta_bubbles": shot_weta_bubbles, "ocean_waves": shot_ocean_waves, "whirlpool": shot_whirlpool, "smoke_plume": shot_smoke_plume, "fire": shot_fire, "smoke_ring": shot_smoke_ring, "bubbles": shot_bubbles,
         "shallow_water": shot_shallow_water, "galaxy": shot_galaxy, "lava_lamp": shot_lava_lamp,
         "cloud": shot_cloud}


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot", required=True)
    ap.add_argument("--frames", type=int, default=120)
    ap.add_argument("--stills", default="")
    ap.add_argument("--sequence", default="")       # e.g. 1-140: render every frame in range
    ap.add_argument("--samples", type=int, default=128)
    ap.add_argument("--voxel", type=float, default=0.0)
    ap.add_argument("--density", type=float, default=8.0)
    ap.add_argument("--res", default="")            # e.g. 540x675 for quick looks
    ap.add_argument("--tag", default="")
    args = ap.parse_args(argv)
    os.makedirs(OUT, exist_ok=True)
    sc, ob, res, src = SHOTS[args.shot](args)
    if args.res:
        sc.render.resolution_x, sc.render.resolution_y = (int(x) for x in args.res.split("x"))
    stills = [int(x) for x in args.stills.split(",") if x]
    tag = args.shot + (f"_{args.tag}" if args.tag else "")

    # 1. timing pass (fresh cache, nothing else running)
    ms = simulate(sc, args.frames)
    stats = {"shot": args.shot, "blender": bpy.app.version_string, "nodes": res.node_count,
             "frames": args.frames, "ms_per_frame_avg": round(sum(ms) / len(ms), 1),
             "ms_per_frame_last20": round(sum(ms[-20:]) / len(ms[-20:]), 1),
             "fps": round(1000 / (sum(ms) / len(ms)), 1), "script": src, "notes": res.notes}
    print("STATS", json.dumps({k: v for k, v in stats.items() if k != "script"}), flush=True)
    with open(os.path.join(OUT, f"{tag}.json"), "w") as f:
        json.dump(stats, f, indent=1)

    # 2. renders (the cache is warm, so frame_set is instant)
    for fr in stills:
        sc.frame_set(fr)
        path = os.path.join(OUT, f"{tag}_f{fr:04d}.png")
        t = time.time()
        render(sc, path, args.samples)
        print("RENDERED", path, round(time.time() - t, 1), "s", flush=True)
    if args.sequence:
        a, b = (int(x) for x in args.sequence.split("-"))
        seq = os.path.join(OUT, f"{tag}_seq")
        os.makedirs(seq, exist_ok=True)
        for fr in range(a, b + 1):
            sc.frame_set(fr)
            render(sc, os.path.join(seq, f"{fr:04d}.png"), args.samples)
            print("SEQ", fr, flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
