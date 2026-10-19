# SPDX-License-Identifier: GPL-3.0-or-later
"""Beginner guide carousel (1080×1350), a web banner and recipe-gallery posters.

    python promo/compose_guide.py <screens folder> <gallery folder> <out folder>

Screens are real Blender 5.3 screenshots taken while doing each step; gallery images come from
promo/recipe_gallery.py (one EEVEE render per recipe) with gallery.json."""

import json
import os
import sys
import textwrap

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from compose_53 import BOLD, MONO, SANS, _font, draw_code, fit, rounded  # noqa: E402
from formula_to_nodes import recipes  # noqa: E402

W, H = 1080, 1350
BG, PANEL, FG, DIM, ACCENT = (13, 14, 18), (26, 28, 35), (236, 238, 243), (150, 156, 170), (255, 163, 72)
TOTAL = 10


def hexrgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def canvas():
    img = Image.new("RGB", (W, H), BG)
    glow = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(glow)
    d.ellipse((-300, -400, 900, 500), fill=(34, 30, 26))
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(160)), 0.9)
    return img, ImageDraw.Draw(img)


def footer(d, idx):
    d.text((60, H - 70), "Formula to Nodes 3.2  ·  Blender 5.3", font=_font(SANS, 22), fill=DIM)
    t = f"{idx} / {TOTAL}"
    d.text((W - 60 - d.textlength(t, font=_font(BOLD, 22)), H - 70), t, font=_font(BOLD, 22), fill=DIM)


def step_head(d, num, title, body, y=70):
    if num:
        d.rounded_rectangle((60, y, 60 + 150, y + 50), 25, fill=ACCENT)
        d.text((60 + 22, y + 9), f"STEP {num}", font=_font(BOLD, 26), fill=BG)
        y += 78
    for line in textwrap.wrap(title, 24):
        d.text((60, y), line, font=_font(BOLD, 62), fill=FG)
        y += 74
    y += 10
    for line in textwrap.wrap(body, 44):
        d.text((62, y), line, font=_font(SANS, 30), fill=DIM)
        y += 42
    return y + 24


def framed(img, w, h, mode="contain", radius=22, border=(60, 64, 76)):
    im = fit(img, w, h, mode)
    frame = Image.new("RGB", (im.width + 4, im.height + 4), border)
    frame.paste(im, (2, 2))
    return rounded(frame, radius)


def paste_center(base, im, y):
    x = (W - im.width) // 2
    base.paste(im, (x, y), im)
    return y + im.height


def callout(d, x, y, text, size=26):
    f = _font(BOLD, size)
    w = d.textlength(text, font=f) + 30
    d.rounded_rectangle((x, y, x + w, y + size + 22), (size + 22) // 2, fill=ACCENT)
    d.text((x + 15, y + 10), text, font=f, fill=BG)


# ─────────────────────────────────────────────────────────────────────────────

def slide_cover(S, G, out):
    img, d = canvas()
    tiles = ["twist", "ocean_waves", "galaxy", "grass", "voronoi_colors", "mountain",
             "connectivity", "wave_pool", "curvature_colors"]
    T = 300
    for i, k in enumerate(tiles):
        p = os.path.join(G, k + ".png")
        if os.path.exists(p):
            im = rounded(fit(Image.open(p).convert("RGB"), T, T), 18)
            img.paste(im, (60 + (i % 3) * (T + 30), 330 + (i // 3) * (T + 30)), im)
    d.text((60, 70), "Formula to Nodes", font=_font(BOLD, 74), fill=FG)
    d.text((62, 160), "Geometry Nodes for absolute beginners", font=_font(SANS, 34), fill=ACCENT)
    d.text((62, 215), "Pick a recipe, move a slider — or type one line.", font=_font(SANS, 30), fill=DIM)
    d.text((62, 255), "No node wiring needed.  Swipe →", font=_font(SANS, 30), fill=DIM)
    footer(d, 1)
    return save(img, out, "01_cover")


def slide_what(S, G, out):
    img, d = canvas()
    y = step_head(d, None, "What is it?",
                  "Geometry Nodes is Blender's way of building shapes, scattering and effects procedurally. "
                  "Normally you wire dozens of nodes. This add-on writes them for you.")
    # script → nodes → result
    code = '@P.z += sin(@P.x * chf("waves", 4))\n       * chf("height", 0.2);'
    card = Image.new("RGB", (960, 150), (20, 22, 28))
    draw_code(ImageDraw.Draw(card), 28, 36, code, size=30, lh=44)
    img.paste(rounded(card, 20), (60, y), rounded(card, 20))
    y += 170
    d.text((W // 2 - 20, y - 4), "↓", font=_font(BOLD, 48), fill=ACCENT)
    y += 64
    nodes = Image.open(os.path.join(S, "s4_nodes.png")).convert("RGB") if os.path.exists(
        os.path.join(S, "s4_nodes.png")) else Image.open(os.path.join(S, "s2_nodes.png")).convert("RGB")
    nodes = nodes.crop((0, 120, int(nodes.width * 0.66), nodes.height - 60))
    im = framed(nodes, 960, 300, "cover")
    img.paste(im, (60, y), im)
    y += im.height + 10
    d.text((W // 2 - 20, y - 4), "↓", font=_font(BOLD, 48), fill=ACCENT)
    y += 64
    vp = Image.open(os.path.join(S, "s4_viewport.png")).convert("RGB")
    im = framed(vp, 960, H - y - 110, "cover")
    img.paste(im, (60, y), im)
    footer(d, 2)
    return save(img, out, "02_what")


def slide_install(S, G, out):
    img, d = canvas()
    y = step_head(d, None, "Install in one minute", "Works in Blender 5.2 and 5.3.")
    steps = [("1", "Download", "add-on-formula-to-nodes-v3.2.0.zip"),
             ("2", "Edit › Preferences › Get Extensions", "⌄ menu (top right) › Install from Disk… › pick the zip"),
             ("3", "Open the Geometry Nodes workspace", "the tab at the top of Blender"),
             ("4", "Press N in the node editor", "click the Formula tab on the right edge")]
    for n, a, b in steps:
        d.ellipse((60, y, 120, y + 60), fill=ACCENT)
        d.text((80, y + 9), n, font=_font(BOLD, 34), fill=BG)
        d.text((145, y + 2), a, font=_font(BOLD, 32), fill=FG)
        d.text((145, y + 44), b, font=_font(SANS, 25), fill=DIM)
        y += 110
    side = Image.open(os.path.join(S, "s1_sidebar.png")).convert("RGB")
    crop = side.crop((1150, 50, 1696, 700))
    im = framed(crop, 600, H - y - 100, "contain")
    x = (W - im.width) // 2
    img.paste(im, (x, y + 10), im)
    callout(d, x + im.width - 120, y + im.height - 60, "Formula tab →", size=22)
    footer(d, 3)
    return save(img, out, "03_install")


def slide_recipe(S, G, out):
    img, d = canvas()
    y = step_head(d, 1, "Pick a recipe",
                  "Select an object, then click + next to a recipe in “Start here”. It's added as a modifier "
                  "straight away.")
    side = Image.open(os.path.join(S, "s1_sidebar.png")).convert("RGB").crop((1160, 340, 1630, 780))
    vp = Image.open(os.path.join(S, "s2_viewport.png")).convert("RGB").crop((300, 60, 1500, 900))
    a = framed(side, 470, 520, "contain")
    b = framed(vp, 470, 520, "cover")
    img.paste(a, (60, y), a)
    img.paste(b, (W - 60 - b.width, y), b)
    d.text((W // 2 - 22, y + a.height // 2 - 30), "→", font=_font(BOLD, 56), fill=ACCENT)
    callout(d, 70, y + a.height + 24, "+ adds “Twist” to the cube")
    y2 = y + a.height + 110
    for line in textwrap.wrap("Colours show the family: purple = deformers, yellow = colour & masks, "
                              "green = scatter, red = simulation, blue = water & volumes, orange = from "
                              "Houdini.", 52):
        d.text((62, y2), line, font=_font(SANS, 27), fill=DIM)
        y2 += 38
    footer(d, 4)
    return save(img, out, "04_recipe")


def slide_sliders(S, G, out):
    img, d = canvas()
    y = step_head(d, 2, "Move the sliders",
                  "Every recipe comes with its own controls in the modifier panel (the wrench tab). "
                  "Drag them — the shape updates live.")
    mod = Image.open(os.path.join(S, "s2_modifier.png")).convert("RGB").crop((60, 180, 609, 560))
    a = framed(mod, 960, 380, "contain")
    y = paste_center(img, a, y) + 30
    vp = Image.open(os.path.join(S, "s2_viewport.png")).convert("RGB")
    b = framed(vp, 960, H - y - 110, "cover")
    paste_center(img, b, y)
    callout(d, 80, y + 20, "angle 120°")
    footer(d, 5)
    return save(img, out, "05_sliders")


def slide_search(S, G, out):
    img, d = canvas()
    y = step_head(d, 3, "Search for anything",
                  "Type what you want — “grass”, “water”, “rock”, “spin”. Filter by category or by "
                  "level: easy, medium, advanced.")
    s = Image.open(os.path.join(S, "s3_search.png")).convert("RGB").crop((1160, 395, 1630, 926))
    a = framed(s, 700, H - y - 110, "contain")
    paste_center(img, a, y)
    footer(d, 6)
    return save(img, out, "06_search")


def slide_write(S, G, out):
    img, d = canvas()
    y = step_head(d, 4, "Write your first line",
                  "Click “Open in Text Editor”, type this, press Ctrl+Enter. It's added to the selected "
                  "object — with two sliders.")
    code = ('// waves across a grid\n'
            '@P.z += sin(@P.x * chf("waves", 4) + @Time * 2)\n'
            '        * chf("height", 0.2);')
    card = Image.new("RGB", (960, 190), (20, 22, 28))
    draw_code(ImageDraw.Draw(card), 28, 30, code, size=29, lh=46)
    img.paste(rounded(card, 20), (60, y), rounded(card, 20))
    y += 215
    legend = [("@P", "the position of every point"), ("sin( )", "a wave"),
              ("chf(\"waves\", 4)", "a slider called waves, starting at 4"), ("@Time", "makes it move")]
    for k, v in legend:
        d.text((70, y), k, font=_font(MONO, 26), fill=(255, 184, 108))
        d.text((380, y), v, font=_font(SANS, 26), fill=DIM)
        y += 40
    y += 16
    vp = Image.open(os.path.join(S, "s4_viewport.png")).convert("RGB").crop((250, 150, 1500, 860))
    mod = Image.open(os.path.join(S, "s4_modifier.png")).convert("RGB").crop((70, 190, 609, 470))
    b = framed(vp, 600, H - y - 110, "cover")
    c = framed(mod, 340, 200, "contain")
    img.paste(b, (60, y), b)
    img.paste(c, (W - 60 - c.width, y), c)
    footer(d, 7)
    return save(img, out, "07_write")


def slide_viewer(S, G, out):
    img, d = canvas()
    y = step_head(d, 5, "The viewer shows the node",
                  "While you type, a card in the node editor shows the node your script becomes: its sliders, "
                  "and live values — or a red message with the line to fix.")
    nodes = Image.open(os.path.join(S, "s2_nodes.png")).convert("RGB").crop((560, 75, 1115, 515))
    a = framed(nodes, 860, 470, "contain")
    y = paste_center(img, a, y) + 26
    tips = ["Values = min / average / max of what the script changes",
            "Mistakes never break your scene — the card just tells you what's wrong",
            "Viewer / Values buttons at the top of the Formula tab"]
    for t in tips:
        d.text((70, y), "•", font=_font(BOLD, 30), fill=ACCENT)
        for i, line in enumerate(textwrap.wrap(t, 50)):
            d.text((100, y + i * 38), line, font=_font(SANS, 28), fill=DIM)
        y += 38 * len(textwrap.wrap(t, 50)) + 14
    footer(d, 8)
    return save(img, out, "08_viewer")


def slide_gallery(S, G, out, meta):
    img, d = canvas()
    d.text((60, 70), "What can you make?", font=_font(BOLD, 62), fill=FG)
    d.text((62, 150), f"{len(recipes.RECIPES)} recipes in {len(recipes.CATEGORIES)} colour-coded families",
           font=_font(SANS, 30), fill=DIM)
    picks = PICKS
    T, gap, cols = 230, 14, 4
    x0, y0 = 60, 220
    names = {"#f1a355": "From Houdini", "#f1dc55": "Masks & colour", "#8d59da": "Deformers",
             "#7bcc7b": "Scatter", "#c673b8": "Effectors", "#e2605b": "Simulation",
             "#5db6ea": "Water & volumes", "#a0745c": "Modeling & curves", "#8c8c8c": "Utility"}
    lx, ly = 60, y0 + 3 * (T + 44 + gap) + 16
    for h, n in names.items():
        w = 34 + d.textlength(n, font=_font(SANS, 23))
        if lx + w > W - 60:
            lx, ly = 60, ly + 40
        d.rounded_rectangle((lx, ly + 4, lx + 22, ly + 26), 5, fill=hexrgb(h))
        d.text((lx + 30, ly), n, font=_font(SANS, 23), fill=FG)
        lx += w + 24
    for i, k in enumerate(picks[:12]):
        p = os.path.join(G, k + ".png")
        if not os.path.exists(p):
            continue
        r = recipes.BY_KEY[k]
        im = fit(Image.open(p).convert("RGB"), T, T)
        tile = Image.new("RGB", (T, T + 44), PANEL)
        tile.paste(im, (0, 0))
        td = ImageDraw.Draw(tile)
        col = hexrgb(recipes.category_hex(r.category))
        td.rectangle((0, T, T, T + 6), fill=col)
        td.text((10, T + 12), r.title[:20], font=_font(BOLD, 19), fill=FG)
        t = rounded(tile, 14)
        x, y = x0 + (i % cols) * (T + gap), y0 + (i // cols) * (T + 44 + gap)
        img.paste(t, (x, y), t)
    footer(d, 9)
    return save(img, out, "09_gallery")


def slide_cheats(S, G, out):
    img, d = canvas()
    y = step_head(d, None, "Cheat sheet", "Everything you need for your first scripts.")
    rows = [("@P", "position (a vector: @P.x  @P.y  @P.z)"), ("@N", "normal — which way a face points"),
            ("@Cd", "colour"), ("@Time  @Frame", "seconds / frame number — for animation"),
            ('chf("name", 1)', "a slider you can change later"), ("sin  cos  noise", "waves and bumpy patterns"),
            ("fit(x, a, b, c, d)", "remap a range"), ("rand(@ptnum)", "a random number per point"),
            ("Ctrl+Enter", "build from the Text Editor"), ("Save as Preset", "keep your script for every file")]
    for k, v in rows:
        d.rounded_rectangle((60, y - 6, W - 60, y + 52), 12, fill=PANEL)
        d.text((80, y + 6), k, font=_font(MONO, 26), fill=(255, 184, 108))
        d.text((430, y + 7), v, font=_font(SANS, 25), fill=FG)
        y += 70
    d.text((62, y + 20), "Stuck? Every recipe is a working example — open it and read it.",
           font=_font(SANS, 27), fill=DIM)
    footer(d, 10)
    return save(img, out, "10_cheatsheet")


PICKS = ["twist", "ocean_waves", "galaxy", "smoke_plume", "grass", "voronoi_colors", "connectivity",
         "poly_extrude", "curvature_colors", "radial_cloner", "flocking", "shallow_water", "rain_ripples",
         "shallow_water", "radial_cloner", "point_relax", "greebles", "island_colors", "copy_prep", "taper"]


def poster_web(S, G, out, meta):
    """1920×1080: every recipe with a usable thumbnail, grouped and colour-coded by family."""
    WW, HH = 1920, 1080
    img = Image.new("RGB", (WW, HH), BG)
    d = ImageDraw.Draw(img)
    d.text((60, 46), f"{len(recipes.RECIPES)} recipes, colour-coded", font=_font(BOLD, 52), fill=FG)
    d.text((62, 112), "Formula to Nodes 3.2 · search them by name, family or level", font=_font(SANS, 26),
           fill=DIM)
    # legend
    x = 60
    fams = []
    for c in recipes.CATEGORIES:
        h = recipes.category_hex(c)
        if h not in [f[1] for f in fams]:
            fams.append((c, h))
    names = {"#f1a355": "From Houdini", "#f1dc55": "Masks & colour", "#8d59da": "Deformers",
             "#7bcc7b": "Scatter", "#c673b8": "Effectors", "#e2605b": "Simulation & fire",
             "#5db6ea": "Water & volumes", "#a0745c": "Modeling & curves", "#8c8c8c": "Utility"}
    for _c, h in fams:
        d.rounded_rectangle((x, 160, x + 22, 182), 5, fill=hexrgb(h))
        d.text((x + 30, 157), names.get(h, _c), font=_font(SANS, 21), fill=FG)
        x += 50 + d.textlength(names.get(h, _c), font=_font(SANS, 21))
    keys = [r.key for c in recipes.CATEGORIES for r in recipes.RECIPES if r.category == c
            and r.key not in SKIP and os.path.exists(os.path.join(G, r.key + ".png"))]
    cols, T, gap = 15, 112, 9
    y0 = 205
    for i, k in enumerate(keys[:cols * 6]):
        r = recipes.BY_KEY[k]
        im = fit(Image.open(os.path.join(G, k + ".png")).convert("RGB"), T, T)
        x, y = 60 + (i % cols) * (T + gap), y0 + (i // cols) * (T + 26 + gap)
        img.paste(im, (x, y))
        d.rectangle((x, y + T, x + T, y + T + 4), fill=hexrgb(recipes.category_hex(r.category)))
        d.text((x + 2, y + T + 6), r.title[:16], font=_font(SANS, 13), fill=DIM)
    return save(img, out, "web_recipes")


SKIP = {"stats", "surface_area", "rest_position", "audio_pulse", "ao_mask", "distance_mask", "magnet",
        "puddles", "explosion", "smoke_from_object", "attrib_transfer", "smooth_union", "remesh_smooth",
        "grow", "shrinkwrap", "effector_push", "spherize"}


def web_guide(S, G, out):
    WW, HH = 1920, 1080
    img = Image.new("RGB", (WW, HH), BG)
    d = ImageDraw.Draw(img)
    d.text((70, 60), "Get started in 4 steps", font=_font(BOLD, 64), fill=FG)
    d.text((72, 150), "Formula to Nodes 3.2 — Geometry Nodes without wiring", font=_font(SANS, 30), fill=ACCENT)
    shots = [("1  Pick a recipe", Image.open(os.path.join(S, "s1_sidebar.png")).crop((1160, 340, 1630, 780))),
             ("2  Move the sliders", Image.open(os.path.join(S, "s2_viewport.png")).crop((300, 60, 1500, 900))),
             ("3  Search anything", Image.open(os.path.join(S, "s3_search.png")).crop((1160, 395, 1630, 926))),
             ("4  Write a line", Image.open(os.path.join(S, "s4_viewport.png")).crop((250, 150, 1500, 860)))]
    x = 70
    for title, im in shots:
        f = framed(im.convert("RGB"), 420, 640, "cover")
        img.paste(f, (x, 260), f)
        d.text((x + 6, 930), title, font=_font(BOLD, 34), fill=FG)
        x += 450
    return save(img, out, "web_getting_started")


def save(img, out, name):
    os.makedirs(out, exist_ok=True)
    p = os.path.join(out, f"{name}.png")
    img.convert("RGB").save(p)
    return p


def main(S, G, out):
    meta = json.load(open(os.path.join(G, "gallery.json"))) if os.path.exists(os.path.join(G, "gallery.json")) else {}
    made = [slide_cover(S, G, out), slide_what(S, G, out), slide_install(S, G, out), slide_recipe(S, G, out),
            slide_sliders(S, G, out), slide_search(S, G, out), slide_write(S, G, out), slide_viewer(S, G, out),
            slide_gallery(S, G, out, meta), slide_cheats(S, G, out), poster_web(S, G, out, meta),
            web_guide(S, G, out)]
    print("\n".join(made))


if __name__ == "__main__":
    main(*sys.argv[1:4])
