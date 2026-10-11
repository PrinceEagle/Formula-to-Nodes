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
    "smoke_ring": ("A vortex ring, no tricks", "One push, the solver does the rest",
                   "No buoyancy, no keyframes: a short push through a disc rolls up into a travelling ring."),
    "bubbles": ("Bubbles that merge and pop", "Particles with real rules",
                "Bigger bubbles rise faster, spiral, swallow their neighbours and pop at the surface."),
    "shallow_water": ("Dam break in 30 lines", "Shallow-water equations, CFL-safe",
                      "Lax–Wendroff on a grid, with substeps derived from the fastest wave so it can't blow up."),
    "galaxy": ("A galaxy from points", "Blender 5.3 Rasterize Points",
               "20,000 points in spiral arms, splatted into glowing dust — colour included."),
    "lava_lamp": ("Lava lamp metaballs", "Points → SDF → smooth → mesh",
                  "Blobs heat, rise, cool and sink; their spheres melt together through a smoothed distance grid."),
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
    if meta.get("fps") and meta.get("fps") < 1000:
        out.append(f"{meta['fps']:.0f} fps live")
    if meta.get("voxels"):
        out.append(f"{meta['voxels'] / 1000:.0f}k voxels")
    out.append("Blender 5.3")
    return out


def insta(shot, meta, render, nodes, out):
    W, H = 1080, 1350
    title, sub, _ = SHOTS[shot]
    img = Image.new("RGB", (W, H), BG)
    hero = fit(render, W, 860)
    img.paste(hero, (0, 0))
    fade = Image.new("L", (1, 220))
    for y in range(220):
        fade.putpixel((0, y), int(255 * (y / 219) ** 1.6))
    img.paste(Image.new("RGB", (W, 220), BG), (0, 640), fade.resize((W, 220)))
    d = ImageDraw.Draw(img)
    d.text((56, 52), "Formula to Nodes", font=_font(BOLD, 30), fill=FG)
    d.text((56, 92), "3.1 · for Blender 5.3", font=_font(SANS, 22), fill=DIM)
    d.text((56, 700), title, font=_font(BOLD, 58), fill=FG)
    d.text((58, 774), sub, font=_font(SANS, 28), fill=DIM)
    # code + nodes row
    y0 = 840
    card = Image.new("RGB", (560, 470), PANEL)
    cd = ImageDraw.Draw(card)
    draw_code(cd, 22, 20, meta["script"], size=13, max_lines=26, lh=17)
    img.paste(rounded(card), (40, y0), rounded(card))
    if nodes is not None:
        nimg = fit(nodes, 420, 470)
        img.paste(rounded(nimg), (620, y0), rounded(nimg))
    x = 40
    f = _font(BOLD, 20)
    for s in stats_text(meta):
        x = badge(d, x, H - 30 - 40 - 0, s, f) if False else x
    d.text((40, H - 34), "  ·  ".join(stats_text(meta)), font=_font(SANS, 20), fill=DIM)
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
        nimg = fit(nodes, 500, H - y - 110, mode="cover")
        img.paste(rounded(nimg), (590, y + 24), rounded(nimg))
    d.text((70, H - 62), "  ·  ".join(stats_text(meta)), font=_font(SANS, 22), fill=DIM)
    path = os.path.join(out, f"{shot}_web.png")
    img.save(path, quality=95)
    return path


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
        nodes_p = os.path.join(folder, f"{shot}_nodes.png")
        nodes = Image.open(nodes_p).convert("RGB") if os.path.exists(nodes_p) else None
        made += [insta(shot, meta, render, nodes, out), story(shot, meta, render, nodes, out),
                 web(shot, meta, render, nodes, out)]
    print("\n".join(made))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "out"))
