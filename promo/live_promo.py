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


# Screenshots need Blender to redraw between changing an editor and capturing it, so they
# take two calls:  show_nodes() / show_viewport()  →  (next call)  shoot("x.png")  →  restore_area()

def show_nodes(sidebar=False, zoom_out=0):
    """Turn the biggest editor into the Geometry Nodes editor showing the shot's tree."""
    ob = bpy.data.objects[_state["ob"]]
    ob.modifiers.active = ob.modifiers[0]          # the editor follows the active modifier
    tree = ob.modifiers[0].node_group
    area = _biggest_area()
    _state.setdefault("area_type", area.ui_type)
    area.ui_type = "GeometryNodeTree"
    space = area.spaces.active
    space.node_tree = tree
    space.show_region_ui = sidebar
    space.show_region_toolbar = False
    if sidebar:
        for r in area.regions:
            if r.type == "UI":
                try:
                    r.active_panel_category = "Formula"
                except Exception:
                    pass
    region = next(r for r in area.regions if r.type == "WINDOW")
    with bpy.context.temp_override(window=bpy.context.window, area=area, region=region):
        bpy.ops.node.select_all(action="DESELECT")
        bpy.ops.node.view_all()
        for _ in range(zoom_out):
            bpy.ops.view2d.zoom_out()
    area.tag_redraw()
    return {"area": area.ui_type, "size": (area.width, area.height), "tree": tree.name}


def show_viewport(rendered=True):
    area = _biggest_area()
    _state.setdefault("area_type", area.ui_type)
    area.ui_type = "VIEW_3D"
    space = area.spaces.active
    space.shading.type = "RENDERED" if rendered else "SOLID"
    space.region_3d.view_perspective = "CAMERA"
    space.overlay.show_overlays = False
    space.show_region_ui = False
    space.show_region_toolbar = False
    area.tag_redraw()
    return {"area": area.ui_type, "size": (area.width, area.height)}


def shoot(path, whole_window=False):
    os.makedirs(OUT, exist_ok=True)
    path = path if os.path.isabs(path) else os.path.join(OUT, path)
    area = _biggest_area()
    region = next(r for r in area.regions if r.type == "WINDOW")
    with bpy.context.temp_override(window=bpy.context.window, area=area, region=region):
        if whole_window:
            bpy.ops.screen.screenshot(filepath=path)
        else:
            bpy.ops.screen.screenshot_area(filepath=path)
    return {"path": path, "bytes": os.path.getsize(path) if os.path.exists(path) else 0}


def _area(kind):
    screen = bpy.context.window.screen
    areas = [a for a in screen.areas if a.type == kind]
    return max(areas, key=lambda a: a.width * a.height) if areas else None


def stage(sidebar=True, viewport="MATERIAL", camera=True, frame=None):
    """Set the Geometry Nodes workspace up for a screenshot: node editor on the shot's tree
    (framed, Formula sidebar open), viewport looking through the camera. Screenshot in the
    next call with shoot_window() — Blender has to redraw in between."""
    ob = bpy.data.objects[_state["ob"]]
    ob.modifiers.active = ob.modifiers[0]
    sc = _scene()
    if frame is not None:
        sc.frame_set(frame)
    out = {}
    ne = _area("NODE_EDITOR")
    if ne is not None:
        if ne.ui_type != "GeometryNodeTree":
            ne.ui_type = "GeometryNodeTree"
        sp = ne.spaces.active
        sp.node_tree = ob.modifiers[0].node_group
        sp.show_region_ui = sidebar
        for r in ne.regions:
            if r.type == "UI" and sidebar:
                try:
                    r.active_panel_category = "Formula"
                except Exception:
                    pass
        region = next(r for r in ne.regions if r.type == "WINDOW")
        with bpy.context.temp_override(window=bpy.context.window, area=ne, region=region):
            bpy.ops.node.select_all(action="DESELECT")
            bpy.ops.node.view_all()
        ne.tag_redraw()
        out["node_editor"] = (ne.x, ne.y, ne.width, ne.height)
        if sidebar:
            out["script_panel"] = load_script_panel()
    v3 = _area("VIEW_3D")
    if v3 is not None:
        sp = v3.spaces.active
        sp.shading.type = viewport
        if camera and sc.camera:
            sp.region_3d.view_perspective = "CAMERA"
        v3.tag_redraw()
        out["viewport"] = (v3.x, v3.y, v3.width, v3.height)
    return out


def load_script_panel():
    """Put the shot's script in the Formula sidebar's Script panel (what a user would see)."""
    ui = next((m for k, m in sys.modules.items() if k.endswith("formula_to_nodes.ui")), None)
    if ui is None or "src" not in _state:
        return False
    try:
        ui.set_script_text(ui.settings(bpy.context), _state["src"])
        return True
    except Exception as e:      # noqa: BLE001
        return str(e)


def toggle_maximize_nodes():
    """Ctrl+Space on the node editor (call again to restore). Redraw before shooting."""
    ne = _area("NODE_EDITOR")
    if ne is None:
        return False
    region = next(r for r in ne.regions if r.type == "WINDOW")
    with bpy.context.temp_override(window=bpy.context.window, area=ne, region=region):
        bpy.ops.screen.screen_full_area()
    return True


def focus_nodes(keyword, hops=1):
    """Frame a readable close-up: the nodes whose label, name or type contains ``keyword``
    plus their neighbours ``hops`` links away. Redraw, then shoot_window()."""
    ne = _area("NODE_EDITOR")
    tree = ne.spaces.active.edit_tree
    key = keyword.lower()
    picked = {n for n in tree.nodes if key in (n.label or "").lower() or key in n.name.lower()
              or key in n.bl_idname.lower()}
    for _ in range(hops):
        more = set()
        for l in tree.links:
            if l.from_node in picked:
                more.add(l.to_node)
            if l.to_node in picked:
                more.add(l.from_node)
        picked |= more
    for n in tree.nodes:
        n.select = n in picked
    region = next(r for r in ne.regions if r.type == "WINDOW")
    with bpy.context.temp_override(window=bpy.context.window, area=ne, region=region):
        bpy.ops.node.view_selected()
    for n in tree.nodes:
        n.select = False
    ne.tag_redraw()
    return len(picked)


def _node_bbox(nodes):
    xs0, xs1, ys0, ys1 = [], [], [], []
    for n in nodes:
        x, y = n.location.x, n.location.y
        w, h = max(n.dimensions.x, n.width), max(n.dimensions.y, 40)
        xs0.append(x); xs1.append(x + w); ys0.append(y - h); ys1.append(y)
    return min(xs0), min(ys0), max(xs1), max(ys1)


def zoom_to_nodes(nodes, pad=80, trim=0.1):
    """Frame these nodes (bounding box + padding, fitted to the editor's aspect), ignoring the
    ``trim`` fraction of outliers at the top and bottom. Uses Zoom Border, which applies
    immediately (View Selected only does on the next redraw)."""
    ne = _area("NODE_EDITOR")
    region = next(r for r in ne.regions if r.type == "WINDOW")
    if trim and len(nodes) > 6:
        ys = sorted(n.location.y for n in nodes)
        lo, hi = ys[int(len(ys) * trim)], ys[int(len(ys) * (1 - trim)) - 1]
        nodes = [n for n in nodes if lo <= n.location.y <= hi] or nodes
    x0, y0, x1, y1 = _node_bbox(nodes)
    x0, y0, x1, y1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
    aspect = region.width / region.height
    w, h = x1 - x0, y1 - y0
    if w / h > aspect:
        cy, h = (y0 + y1) / 2, w / aspect
        y0, y1 = cy - h / 2, cy + h / 2
    else:
        cx, w = (x0 + x1) / 2, h * aspect
        x0, x1 = cx - w / 2, cx + w / 2
    v2 = region.view2d
    for _ in range(3):              # zoom border is relative to the current view: converge
        px0, py0 = v2.view_to_region(x0, y0, clip=False)
        px1, py1 = v2.view_to_region(x1, y1, clip=False)
        with bpy.context.temp_override(window=bpy.context.window, area=ne, region=region):
            bpy.ops.view2d.zoom_border(xmin=int(px0), xmax=int(px1), ymin=int(py0), ymax=int(py1),
                                       wait_for_input=False, zoom_out=False)
    ne.tag_redraw()
    return {"view": (v2.region_to_view(0, 0), v2.region_to_view(region.width, region.height))}


def zoom_fraction(a, b, pad=200):
    """Frame the slice of the tree between fractions a..b of its width (0 = left, 1 = right)."""
    ne = _area("NODE_EDITOR")
    nodes = [n for n in ne.spaces.active.edit_tree.nodes if n.type not in ("FRAME", "REROUTE")]
    xs = [n.location.x for n in nodes]
    lo, hi = min(xs), max(xs)
    pick = [n for n in nodes if lo + a * (hi - lo) <= n.location.x <= lo + b * (hi - lo)]
    out = zoom_to_nodes(pick, pad)
    out["nodes"] = len(pick)
    return out


def frame_nodes():
    """Second pass of view_all once the tree has been drawn (node sizes are known then)."""
    ne = _area("NODE_EDITOR")
    region = next(r for r in ne.regions if r.type == "WINDOW")
    with bpy.context.temp_override(window=bpy.context.window, area=ne, region=region):
        bpy.ops.node.view_all()
    ne.tag_redraw()
    return True


def shoot_window(path):
    """Screenshot of the whole Blender window, plus where each editor is (for cropping)."""
    os.makedirs(OUT, exist_ok=True)
    path = path if os.path.isabs(path) else os.path.join(OUT, path)
    with bpy.context.temp_override(window=bpy.context.window):
        bpy.ops.screen.screenshot(filepath=path)
    win = bpy.context.window
    areas = [{"type": a.ui_type, "x": a.x, "y": a.y, "w": a.width, "h": a.height,
              "regions": [{"type": r.type, "x": r.x, "y": r.y, "w": r.width, "h": r.height} for r in a.regions
                          if r.type in ("UI", "WINDOW") and r.width > 1]}
             for a in win.screen.areas]
    import json
    meta = {"window": (win.width, win.height), "areas": areas}
    json.dump(meta, open(os.path.splitext(path)[0] + ".areas.json", "w"), indent=1)
    return {"path": path, "bytes": os.path.getsize(path), "window": meta["window"]}


def restore_area():
    area = _biggest_area()
    t = _state.pop("area_type", None)
    if t:
        area.ui_type = t
    return t


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
