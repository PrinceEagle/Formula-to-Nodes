# SPDX-License-Identifier: GPL-3.0-or-later
"""Instagram (1080×1350 and 1080×1920 story) and web (1920×1080) promo cards for
Formula to Nodes 3.1: the Cycles render, the real script, the node tree it
compiles to (a Blender screenshot) and measured numbers. Needs Pillow.

    python promo/compose_53.py out/          # reads out/<shot>.json + images, writes out/cards/
"""

import json
import os
import re
import sys
import textwrap

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))


def _font(names, size):
    dirs = ["/usr/share/fonts/truetype/dejavu", "/System/Library/Fonts", "/Library/Fonts",
            "/System/Library/Fonts/Supplemental"]
    for n in names:
        for d in dirs:
            p = os.path.join(d, n)
            if os.path.exists(p):
                return ImageFont.truetype(p, size)
    return ImageFont.load_default()


MONO = ["DejaVuSansMono.ttf", "Menlo.ttc", "SFNSMono.ttf"]
SANS = ["DejaVuSans.ttf", "HelveticaNeue.ttc", "Helvetica.ttc"]
BOLD = ["DejaVuSans-Bold.ttf", "HelveticaNeue.ttc", "Helvetica.ttc"]

BG, PANEL, FG, DIM, ACCENT = (12, 13, 17), (24, 26, 33), (232, 234, 240), (138, 144, 160), (255, 160, 60)
COLORS = {"attr": (255, 184, 108), "func": (110, 190, 255), "num": (190, 150, 255), "str": (152, 220, 130),
          "kw": (255, 121, 168), "com": (105, 112, 128)}
TOKEN = re.compile(r'(?P<com>//.*|#\w+.*)|(?P<str>"[^"]*")|(?P<attr>[fivbspcu34]?@\w+)|'
                   r'(?P<kw>\b(?:if|else|for|foreach|float|int|vector|vector4|bool|return|runover|simulate|'
                   r'init|repeat)\b)|(?P<func>\b\w+(?=\())|(?P<num>\b\d+(?:\.\d+)?\b)')

SHOTS = {
    "smoke_plume": ("3D smoke, typed", "A real smoke solver on OpenVDB grids",
                    "Buoyancy, curl-noise turbulence and vorticity confinement in one Volume Wrangle. "
                    "Blender 5.3's Poisson Solver keeps it incompressible in a single node."),
    "fire": ("Fire from a formula", "Fuel → heat + soot, rendered as blackbody",
             "Fuel pours from a disc and burns; the heat lifts the flames and cools into smoke."),
    "smoke_ring": ("One push → a vortex ring", "No buoyancy, no keyframes",
                   "No buoyancy, no keyframes: a short push through a disc rolls up into a travelling ring."),
    "bubbles": ("Bubbles that merge and pop", "Particles with real rules",
                "Bigger bubbles rise faster, spiral, swallow their neighbours and pop at the surface."),
    "shallow_water": ("Dam break, one script", "Shallow-water equations, CFL-safe",
                      "MacCormack predictor–corrector on a grid, with substeps derived from the fastest wave "
                      "so it can't blow up."),
    "galaxy": ("A galaxy from points", "Blender 5.3 Rasterize Points",
               "20,000 points in spiral arms, splatted into glowing dust — colour included."),
    "lava_lamp": ("Lava lamp metaballs", "Points → SDF → smooth → mesh",
                  "Blobs heat, rise, cool and sink; their spheres melt together through a smoothed distance grid."),
    "ocean_waves": ("Open ocean in one loop", "Six Gerstner waves, summed",
                    "Sharp crests, round troughs and the right speed for every wavelength — foam where they peak."),
    "ocean_waves": ("An ocean in one loop", "Six Gerstner waves, no simulation",
                    "Sharp crests, round troughs and waves that travel at the right speed for their length."),
    "whirlpool": ("Whirlpool", "A free vortex in a few lines",
                  "The surface dips like a real vortex and a spiral pattern turns around the core."),
    "cloud": ("Any mesh → a cloud", "Distance field + billowing noise",
              "Model the shape with a few spheres; the script erodes it into a cumulus."),
}


def code_lines(src, max_lines):
    lines = [l for l in src.strip().splitlines()]
    out = []
    for l in lines:
        if len(l) > 64:
            ind = len(l) - len(l.lstrip())
            parts = textwrap.wrap(l.strip(), 64 - ind)
            out.append(" " * ind + parts[0])
            out += [" " * (ind + 4) + p for p in parts[1:]]
        else:
            out.append(l)
    if len(out) > max_lines:
        out = out[:max_lines - 1] + ["    …"]
    return out


def draw_code(d, x, y, src, size=17, max_lines=40, lh=None):
    font = _font(MONO, size)
    lh = lh or int(size * 1.45)
    for line in code_lines(src, max_lines):
        cx, pos = x, 0
        for m in TOKEN.finditer(line):
            if m.start() > pos:
                d.text((cx, y), line[pos:m.start()], font=font, fill=FG)
                cx += d.textlength(line[pos:m.start()], font=font)
            d.text((cx, y), m.group(0), font=font, fill=COLORS[m.lastgroup])
            cx += d.textlength(m.group(0), font=font)
            pos = m.end()
        d.text((cx, y), line[pos:], font=font, fill=FG)
        y += lh
    return y


def fit(img, w, h, mode="cover"):
    r = max(w / img.width, h / img.height) if mode == "cover" else min(w / img.width, h / img.height)
    im = img.resize((max(1, int(img.width * r)), max(1, int(img.height * r))), Image.LANCZOS)
    if mode == "cover":
        x, y = (im.width - w) // 2, (im.height - h) // 2
        return im.crop((x, y, x + w, y + h))
    return im


def rounded(img, radius=22):
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius, fill=255)
    out = Image.new("RGBA", img.size)
    out.paste(img.convert("RGBA"), (0, 0), mask)
    return out


def badge(d, x, y, text, font, fg=BG, bg=ACCENT):
    w = d.textlength(text, font=font) + 28
    d.rounded_rectangle((x, y, x + w, y + 40), 20, fill=bg)
    d.text((x + 14, y + 7), text, font=font, fill=fg)
    return x + w + 12


def stats_text(meta):
    out = []
    if meta.get("nodes"):
        out.append(f"{meta['nodes']} nodes")
    if meta.get("voxels"):
        out.append(f"{meta['voxels'] / 1000:.0f}k voxels")
    fps = meta.get("fps")
    if fps and fps < 1000:
        out.append(f"{fps:.1f} fps simulated" if fps < 10 else f"{fps:.0f} fps simulated")
    v = meta.get("blender", "")
    out.append("Blender " + (v.replace(" LTS", "").replace(" Beta", " beta") if v else "5.3"))
    return out


def insta(shot, meta, render, nodes, out):
    W, H = 1080, 1350
    title, sub, _ = SHOTS[shot]
    img = Image.new("RGB", (W, H), BG)
    hero = 800 if nodes is not None else 1036
    img.paste(fit(render, W, hero), (0, 0))
    fade = Image.new("L", (1, 240))
    for y in range(240):
        fade.putpixel((0, y), int(255 * (y / 239) ** 1.6))
    img.paste(Image.new("RGB", (W, 240), BG), (0, hero - 240), fade.resize((W, 240)))
    ty = 640 if nodes is not None else 876
    top = Image.new("L", (1, 170))
    for y in range(170):
        top.putpixel((0, y), int(200 * (1 - y / 169) ** 1.4))
    img.paste(Image.new("RGB", (W, 170), BG), (0, 0), top.resize((W, 170)))
    d = ImageDraw.Draw(img)
    d.text((48, 44), "Formula to Nodes", font=_font(BOLD, 30), fill=FG)
    d.text((48, 84), "3.1 · for Blender 5.3", font=_font(SANS, 22), fill=DIM)
    d.text((48, ty), title, font=_font(BOLD, 56), fill=FG)
    d.text((50, ty + 72), sub, font=_font(SANS, 27), fill=DIM)
    # the node tree the script built (Blender screenshot) and the first lines of the script
    if nodes is not None:
        nimg = panel_image(nodes, 984, 250)
        img.paste(rounded(nimg, 18), (48, 772), rounded(nimg, 18))
        d.text((64, 782), "the node tree it built", font=_font(SANS, 16), fill=DIM)
    card = Image.new("RGB", (984, 238), PANEL)
    lines = [l for l in meta["script"].strip().splitlines() if l.strip() and not l.strip().startswith("//")]
    draw_code(ImageDraw.Draw(card), 22, 16, "\n".join(lines[:11]), size=14, max_lines=11, lh=19)
    img.paste(rounded(card, 18), (48, 1036), rounded(card, 18))
    d.text((48, H - 44), "  ·  ".join(stats_text(meta)), font=_font(SANS, 21), fill=DIM)
    path = os.path.join(out, f"{shot}_insta.png")
    img.save(path, quality=95)
    return path


def story(shot, meta, render, nodes, out):
    W, H = 1080, 1920
    title, sub, body = SHOTS[shot]
    img = Image.new("RGB", (W, H), BG)
    img.paste(fit(render, W, 1180), (0, 0))
    fade = Image.new("L", (1, 260))
    for y in range(260):
        fade.putpixel((0, y), int(255 * (y / 259) ** 1.5))
    img.paste(Image.new("RGB", (W, 260), BG), (0, 920), fade.resize((W, 260)))
    top = Image.new("L", (1, 220))
    for y in range(220):
        top.putpixel((0, y), int(200 * (1 - y / 219) ** 1.4))
    img.paste(Image.new("RGB", (W, 220), BG), (0, 0), top.resize((W, 220)))
    d = ImageDraw.Draw(img)
    d.text((64, 80), "Formula to Nodes 3.1", font=_font(BOLD, 34), fill=FG)
    d.text((64, 1010), title, font=_font(BOLD, 64), fill=FG)
    d.text((66, 1092), sub, font=_font(SANS, 30), fill=DIM)
    card = Image.new("RGB", (952, 560), PANEL)
    draw_code(ImageDraw.Draw(card), 28, 26, meta["script"], size=16, max_lines=27, lh=19)
    img.paste(rounded(card), (64, 1170), rounded(card))
    d.text((64, 1770), "  ·  ".join(stats_text(meta)), font=_font(SANS, 26), fill=DIM)
    d.text((64, 1820), "Type the formula. Get the node group.", font=_font(BOLD, 30), fill=ACCENT)
    path = os.path.join(out, f"{shot}_story.png")
    img.save(path, quality=95)
    return path


def web(shot, meta, render, nodes, out):
    W, H = 1920, 1080
    title, sub, body = SHOTS[shot]
    img = Image.new("RGB", (W, H), BG)
    img.paste(fit(render, 820, H), (W - 820, 0))
    d = ImageDraw.Draw(img)
    d.text((70, 56), "Formula to Nodes 3.1", font=_font(BOLD, 30), fill=DIM)
    d.text((70, 104), title, font=_font(BOLD, 56), fill=FG)
    y = 186
    for line in textwrap.wrap(body, 62):
        d.text((72, y), line, font=_font(SANS, 24), fill=DIM)
        y += 34
    card = Image.new("RGB", (500, H - y - 110), PANEL)
    draw_code(ImageDraw.Draw(card), 22, 20, meta["script"], size=12, max_lines=60, lh=15)
    img.paste(rounded(card), (70, y + 24), rounded(card))
    if nodes is not None:
        nimg = panel_image(nodes, 500, H - y - 110)
        img.paste(rounded(nimg), (590, y + 24), rounded(nimg))
    d.text((70, H - 62), "  ·  ".join(stats_text(meta)), font=_font(SANS, 22), fill=DIM)
    path = os.path.join(out, f"{shot}_web.png")
    img.save(path, quality=95)
    return path


def editor_crop(folder, shot, kind):
    """Crop the node editor (or 3D viewport) out of a full-window Blender screenshot,
    using the region rectangles live_promo.shoot_window() saved next to it."""
    img_p = os.path.join(folder, f"{shot}_{kind}.png")
    meta_p = os.path.splitext(img_p)[0] + ".areas.json"
    if not (os.path.exists(img_p) and os.path.exists(meta_p)):
        return None
    img = Image.open(img_p).convert("RGB")
    meta = json.load(open(meta_p))
    ww, wh = meta["window"]
    sx, sy = img.width / ww, img.height / wh
    want = "GeometryNodeTree"
    area = max((a for a in meta["areas"] if a["type"] == want), key=lambda a: a["w"] * a["h"], default=None)
    if area is None:
        return img
    reg = next((r for r in area["regions"] if r["type"] == "WINDOW"), area)
    # region coords are bottom-left based, in pixels (already Retina-scaled when > window size)
    k = 1.0 if area["w"] > ww else sx
    x0, y0, w, h = reg["x"] * k, reg["y"] * k, reg["w"] * k, reg["h"] * k
    ui = next((r for r in area["regions"] if r["type"] == "UI" and r["w"] > 1), None)
    if ui is not None and x0 < ui["x"] * k < x0 + w:          # the sidebar overlaps the editor
        w = ui["x"] * k - x0
    top = img.height - (y0 + h)
    return img.crop((int(x0), int(top), int(x0 + w), int(top + h)))


def autocrop(img, pad=40, min_frac=0.02):
    """Trim the empty editor background around the nodes."""
    import numpy as np
    a = np.asarray(img.convert("RGB")).astype(int)
    bg = np.median(a.reshape(-1, 3), axis=0)
    busy = (np.abs(a - bg).max(axis=2) > 30)
    busy[:90] = False                       # the editor's breadcrumb text
    busy[-40:] = False
    # rows/columns where nodes are (the dot grid alone is too sparse to count)
    rows, cols = np.where(busy.mean(axis=1) > 0.006)[0], np.where(busy.mean(axis=0) > 0.006)[0]
    if len(rows) < img.height * min_frac or len(cols) < img.width * min_frac:
        return img
    y0, y1 = max(rows[0] - pad, 0), min(rows[-1] + pad, img.height)
    x0, x1 = max(cols[0] - pad, 0), min(cols[-1] + pad, img.width)
    return img.crop((x0, y0, x1, y1))


def panel_image(img, w, h, bg=(29, 29, 29)):
    """Fit a node screenshot into a w×h panel without cutting nodes off."""
    img = autocrop(img)
    fitted = fit(img, w, h, mode="contain")
    out = Image.new("RGB", (w, h), bg)
    out.paste(fitted, ((w - fitted.width) // 2, (h - fitted.height) // 2))
    return out


def main(folder):
    out = os.path.join(folder, "cards")
    os.makedirs(out, exist_ok=True)
    made = []
    for shot in SHOTS:
        meta_p = os.path.join(folder, f"{shot}.json")
        if not os.path.exists(meta_p):
            continue
        meta = json.load(open(meta_p))
        render_p = meta.get("render") or next((os.path.join(folder, f) for f in sorted(os.listdir(folder))
                                               if f.startswith(shot + "_f") and f.endswith(".png")), None)
        if not render_p or not os.path.exists(os.path.join(folder, os.path.basename(render_p))):
            continue
        render = Image.open(os.path.join(folder, os.path.basename(render_p))).convert("RGB")
        nodes = editor_crop(folder, shot, "closeup") or editor_crop(folder, shot, "nodes")
        made += [insta(shot, meta, render, nodes, out), story(shot, meta, render, nodes, out),
                 web(shot, meta, render, nodes, out)]
    print("\n".join(made))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "out"))
