# SPDX-License-Identifier: GPL-3.0-or-later
"""Draws a node group dumped by node_map.py in the style of Blender's node editor, with the
Blender 5.3 nodes outlined and labelled. The layout is the add-on's real layout."""

import json
import math

from PIL import Image, ImageDraw, ImageFilter

NEW_53 = {"GeometryNodeRasterizePoints": "Rasterize Points", "GeometryNodeGridSolvePoisson": "Poisson Solver",
          "GeometryNodeCombineList": "Combine List", "FunctionNodeGetVectorComponent": "Get Vector Component",
          "GeometryNodeSampleGridTricubic": "Tricubic Sample"}
ACCENT = (255, 196, 120)


def header_color(t):
    if t in NEW_53 or "Grid" in t or "Volume" in t or "SDF" in t or "Rasterize" in t:
        return (40, 120, 110)
    if t in ("GeometryNodeSimulationInput", "GeometryNodeSimulationOutput", "GeometryNodeRepeatInput",
             "GeometryNodeRepeatOutput"):
        return (150, 80, 160)
    if t.startswith("ShaderNodeMath") or t.startswith("ShaderNodeVectorMath") or t.startswith("FunctionNodeMatrix") \
            or "Matrix" in t or "Transpose" in t or "Invert" in t or "Transform" in t or "Compare" in t \
            or "Switch" in t or "Mix" in t or "Combine" in t or "Separate" in t or "Clamp" in t or "MapRange" in t:
        return (40, 85, 135)
    if t.startswith("GeometryNodeInput") or t.startswith("FunctionNodeInput") or "Index" in t or "SceneTime" in t \
            or "Position" in t or "NamedAttribute" in t:
        return (140, 50, 55)
    if t in ("NodeGroupInput", "NodeGroupOutput"):
        return (60, 60, 60)
    return (30, 110, 70)


def draw_nodemap(path, W, H, bg=(29, 29, 29), label_font=None, small_font=None, pad=40):
    d = json.load(open(path))
    nodes = d["nodes"]
    boxes = []
    for n in nodes:
        h = 30 + 22 * max(n["rows"], 1)
        # Blender locations are the top-left corner, y up
        boxes.append((n["x"], -n["y"], n["x"] + n["w"], -n["y"] + h))
    real = [b for b, n in zip(boxes, nodes) if not n["frame"] and n["type"] != "NodeReroute"]
    x0 = min(b[0] for b in real)
    y0 = min(b[1] for b in real)
    x1 = max(b[2] for b in real)
    y1 = max(b[3] for b in real)
    # a long left-to-right graph is wrapped into rows (like reading lines) to fill the card
    aspect = (x1 - x0) / max(y1 - y0, 1)
    k = max(1, min(4, round(math.sqrt(aspect / (W / H)))))
    band = (x1 - x0) / k + 1e-6
    gap = (y1 - y0) * 0.25
    band_of = [min(k - 1, max(0, int((b[0] - x0) / band))) for b in boxes]
    lo_y = [min((b[1] for b, i, n in zip(boxes, band_of, nodes) if i == j and not n["frame"]), default=0)
            for j in range(k)]
    hi_y = [max((b[3] for b, i, n in zip(boxes, band_of, nodes) if i == j and not n["frame"]), default=0)
            for j in range(k)]
    gap = max(hi - lo for lo, hi in zip(lo_y, hi_y)) * 0.12
    top = [0.0] * k
    for j in range(1, k):
        top[j] = top[j - 1] + (hi_y[j - 1] - lo_y[j - 1]) + gap
    wrapped = []
    for b, i in zip(boxes, band_of):
        dx, dy = -i * band, top[i] - lo_y[i] + y0
        wrapped.append((b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy))
    boxes = wrapped
    real = [b for b, n in zip(boxes, nodes) if not n["frame"] and n["type"] != "NodeReroute"]
    x0 = min(b[0] for b in real)
    y0 = min(b[1] for b in real)
    x1 = max(b[2] for b in real)
    y1 = max(b[3] for b in real)
    s = min((W - 2 * pad) / (x1 - x0), (H - 2 * pad) / (y1 - y0))
    ox = pad + ((W - 2 * pad) - (x1 - x0) * s) / 2
    oy = pad + ((H - 2 * pad) - (y1 - y0) * s) / 2

    def tr(x, y):
        return ox + (x - x0) * s, oy + (y - y0) * s

    img = Image.new("RGB", (W, H), bg)
    g = ImageDraw.Draw(img)
    # dotted grid like the editor
    step = max(12, int(40 * s * 4))
    for gx in range(0, W, step):
        for gy in range(0, H, step):
            g.point((gx, gy), fill=(40, 40, 40))
    # frames
    for b, n in zip(boxes, nodes):
        if n["frame"]:
            continue
    # links (bezier-ish)
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(lay)
    for a, bidx in d["links"]:
        ba, bb = boxes[a], boxes[bidx]
        if nodes[a]["frame"] or nodes[bidx]["frame"] or band_of[a] != band_of[bidx]:
            continue
        p0 = tr(ba[2], (ba[1] + ba[3]) / 2)
        p3 = tr(bb[0], (bb[1] + bb[3]) / 2)
        dx = max(abs(p3[0] - p0[0]) * 0.5, 6)
        pts = []
        for i in range(17):
            t = i / 16
            c1 = (p0[0] + dx, p0[1])
            c2 = (p3[0] - dx, p3[1])
            x = (1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * c1[0] + 3 * (1 - t) * t * t * c2[0] + t ** 3 * p3[0]
            y = (1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * c1[1] + 3 * (1 - t) * t * t * c2[1] + t ** 3 * p3[1]
            pts.append((x, y))
        ld.line(pts, fill=(150, 150, 150, 90), width=1)
    img.paste(lay, (0, 0), lay)
    g = ImageDraw.Draw(img)
    highlights = []
    for b, n in zip(boxes, nodes):
        if n["frame"] or n["type"] == "NodeReroute":
            continue
        ax, ay = tr(b[0], b[1])
        bx, by = tr(b[2], b[3])
        hh = max(2, 30 * s)
        r = max(1, int(5 * s))
        g.rounded_rectangle((ax, ay, bx, by), r, fill=(48, 48, 48))
        g.rounded_rectangle((ax, ay, bx, ay + hh), r, fill=header_color(n["type"]))
        if n["type"] in NEW_53:
            highlights.append((ax, ay, bx, by, NEW_53[n["type"]]))
    # 5.3 nodes: glow + outline + label
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for ax, ay, bx, by, _ in highlights:
        gd.rounded_rectangle((ax - 6, ay - 6, bx + 6, by + 6), 8, outline=ACCENT + (200,), width=6)
    glow = glow.filter(ImageFilter.GaussianBlur(6))
    img.paste(glow, (0, 0), glow)
    g = ImageDraw.Draw(img)
    placed = []
    for ax, ay, bx, by, name in highlights:
        g.rounded_rectangle((ax - 2, ay - 2, bx + 2, by + 2), 4, outline=ACCENT, width=2)
        if label_font is None:
            continue
        key = (name, round(ax / 120), round(ay / 60))
        if any(abs(px - ax) < 150 and abs(py - ay) < 34 for px, py in placed):
            continue
        placed.append((ax, ay))
        tw = g.textlength(name, font=label_font)
        tx = min(max(ax, 8), W - tw - 16)
        ty = ay - 34 if ay > 50 else by + 6
        g.rounded_rectangle((tx - 6, ty - 2, tx + tw + 6, ty + 26), 6, fill=(20, 20, 22))
        g.text((tx, ty), name, font=label_font, fill=ACCENT)
    return img, d
