# SPDX-License-Identifier: GPL-3.0-or-later
"""Promo shots built inside the Blender file you have open (run through an MCP
client or the Python console). Each shot gets its own scene, "FTN <shot>", so
your other scenes are never touched; switch back with the scene selector.

    import sys; sys.path.insert(0, "<repo>/promo"); import live_promo as L
    L.build("smoke_plume")           # scene + recipe + camera, lights and materials
    L.simulate(120)                  # bake the sim into memory, returns ms per frame
    L.node_shot("smoke_nodes.png")   # screenshot of the node tree in a node editor
    L.panel_shot("smoke_panel.png")  # the Formula sidebar with the script
    L.render("smoke_f120.png", frame=120, samples=96)
    L.restore()                      # back to the scene and editors you had
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bpy  # noqa: E402

import sims  # noqa: E402

OUT = os.path.join(HERE, "out")
_state = {}


class _Args:
    def __init__(self, **kw):
        self.voxel = kw.get("voxel", 0.0)
        self.density = kw.get("density", 8.0)


def build(shot, **kw):
    """Build one promo shot into scene 'FTN <shot>' of the open file and show it."""
    win = bpy.context.window
    if "scene" not in _state:
        _state["scene"] = win.scene.name
    sims.LIVE_SCENE = f"FTN {shot}"
    sc, ob, res, src = sims.SHOTS[shot](_Args(**kw))
    win.scene = sc
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    _state.update(shot=shot, sc=sc.name, ob=ob.name, src=src, nodes=res.node_count)
    return {"scene": sc.name, "object": ob.name, "nodes": res.node_count, "notes": res.notes}


def _scene():
    return bpy.data.scenes[_state["sc"]]


def simulate(frames):
    sc = _scene()
    ms = sims.simulate(sc, frames)
    avg = sum(ms) / len(ms)
    _state["timing"] = {"frames": frames, "ms_per_frame": round(avg, 1), "fps": round(1000 / avg, 1)}
    return _state["timing"]


def _biggest_area():
    screen = bpy.context.window.screen
    return max((a for a in screen.areas if a.type not in ("TOPBAR", "STATUSBAR")),
               key=lambda a: a.width * a.height)


def _shot_area(path, setup):
    """Temporarily turn the biggest area into another editor, screenshot it, put it back."""
    os.makedirs(OUT, exist_ok=True)
    path = path if os.path.isabs(path) else os.path.join(OUT, path)
    area = _biggest_area()
    saved = area.ui_type
    try:
        setup(area)
        region = next(r for r in area.regions if r.type == "WINDOW")
        with bpy.context.temp_override(window=bpy.context.window, area=area, region=region):
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
            bpy.ops.screen.screenshot_area(filepath=path)
    finally:
        area.ui_type = saved
    return path


def node_shot(path, show_sidebar=False):
    """The Geometry Nodes tree of the shot's modifier, framed to fit."""
    ob = bpy.data.objects[_state["ob"]]
    tree = ob.modifiers[0].node_group

    def setup(area):
        area.ui_type = "GeometryNodeTree"
        space = area.spaces.active
        space.pin = False
        space.node_tree = tree
        space.show_region_ui = show_sidebar
        region = next(r for r in area.regions if r.type == "WINDOW")
        with bpy.context.temp_override(window=bpy.context.window, area=area, region=region):
            bpy.ops.node.select_all(action="DESELECT")
            bpy.ops.node.view_all()
    return _shot_area(path, setup)


def panel_shot(path):
    """Node editor with the Formula sidebar open (script, parameters, recipes)."""
    return node_shot(path, show_sidebar=True)


def viewport_shot(path):
    """The 3D viewport, rendered shading, through the shot's camera."""
    def setup(area):
        area.ui_type = "VIEW_3D"
        space = area.spaces.active
        space.shading.type = "RENDERED"
        space.region_3d.view_perspective = "CAMERA"
        space.overlay.show_overlays = False
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=3)
    return _shot_area(path, setup)


def render(path, frame=None, samples=96, res=None):
    sc = _scene()
    if frame is not None:
        sc.frame_set(frame)
    if res:
        sc.render.resolution_x, sc.render.resolution_y = res
    path = path if os.path.isabs(path) else os.path.join(OUT, path)
    t = time.time()
    sims.render(sc, path, samples)
    return {"path": path, "seconds": round(time.time() - t, 1)}


def info():
    return {k: v for k, v in _state.items() if k != "src"}


def restore():
    """Back to the scene you had open before the promo shots."""
    name = _state.get("scene")
    if name and name in bpy.data.scenes:
        bpy.context.window.scene = bpy.data.scenes[name]
    return name
