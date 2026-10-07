# SPDX-License-Identifier: GPL-3.0-or-later
"""Composes promo cards (script + Cycles render) from render_promo.py's output.
Needs Pillow. These are composites, not screenshots of the Blender UI."""
import json
import os
import re

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONTS = "/usr/share/fonts/truetype/dejavu"
MONO = ImageFont.truetype(os.path.join(FONTS, "DejaVuSansMono.ttf"), 19)
SANS_B = ImageFont.truetype(os.path.join(FONTS, "DejaVuSans-Bold.ttf"), 46)
SANS = ImageFont.truetype(os.path.join(FONTS, "DejaVuSans.ttf"), 26)
SMALL = ImageFont.truetype(os.path.join(FONTS, "DejaVuSans.ttf"), 20)

BG, PANEL, FG, DIM = (16, 17, 22), (27, 29, 37), (222, 225, 233), (128, 134, 150)
COLORS = {"attr": (255, 184, 108), "func": (110, 190, 255), "num": (190, 150, 255), "str": (152, 220, 130),
          "kw": (255, 121, 168), "com": (110, 116, 130)}
TOKEN = re.compile(r'(?P<com>//.*|#\w+.*)|(?P<str>"[^"]*")|(?P<attr>[fivbspcu34]?@\w+)|'
                   r'(?P<kw>\b(?:if|else|for|foreach|float|int|vector|vector4|return|runover|simulate)\b)|'
                   r'(?P<func>\b\w+(?=\())|(?P<num>\b\d+(?:\.\d+)?\b)')
TITLES = {"voronoi": ("Voronoi cell colours", "One line, per-face: cellrand() + hsvtorgb()"),
          "greebles": ("Greebles", "Random per-face extrusion, coloured by height with a ramp preset"),
          "displace": ("Noise displacement", "Signed noise along normals, stored and colour-mapped")}


def _wrap(text, width=70):
    out = []
    for line in text.splitlines():
        indent = len(line) - len(line.lstrip())
        while len(line) > width:
            cut = max(line.rfind(" ", 0, width), line.rfind(",", 0, width) + 1, indent + 10)
            out.append(line[:cut].rstrip())
            line = " " * (indent + 8) + line[cut:].lstrip()
        out.append(line)
    return out


def draw_code(d, x, y, text):
    for line in _wrap(text):
        cx = x
        pos = 0
        for m in TOKEN.finditer(line):
            if m.start() > pos:
                d.text((cx, y), line[pos:m.start()], font=MONO, fill=FG)
                cx += d.textlength(line[pos:m.start()], font=MONO)
            d.text((cx, y), m.group(0), font=MONO, fill=COLORS[m.lastgroup])
            cx += d.textlength(m.group(0), font=MONO)
            pos = m.end()
        d.text((cx, y), line[pos:], font=MONO, fill=FG)
        y += 28
    return y


def card(key, shot):
    W, H = 1920, 1080
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((70, 60), "Formula to Nodes 3.0", font=SANS_B, fill=FG)
    title, sub = TITLES.get(key, (key, ""))
    d.text((70, 128), f"{title} — {sub}", font=SANS, fill=DIM)
    d.rounded_rectangle((60, 200, 860, 1000), radius=18, fill=PANEL)
    y = 235
    for i, src in enumerate(shot["scripts"]):
        d.text((90, y), f"Formula modifier {i + 1}", font=SMALL, fill=DIM)
        y = draw_code(d, 90, y + 34, src) + 30
    render = Image.open(os.path.join(HERE, shot["image"])).convert("RGB")
    render = render.resize((1000, 750), Image.LANCZOS)
    img.paste(render, (890, 225))
    d.text((70, 1022), "Real scripts compiled to Geometry Nodes by the add-on, rendered in Cycles (Blender 5.2).",
           font=SMALL, fill=DIM)
    path = os.path.join(HERE, f"card_{key}.png")
    img.save(path)
    return img


def main():
    shots = json.load(open(os.path.join(HERE, "shots.json")))
    cards = [card(k, v) for k, v in shots.items()]
    sheet = Image.new("RGB", (1920, 1080 * len(cards) // 2 + 0), BG)
    for i, c in enumerate(cards):
        sheet.paste(c.resize((960, 540), Image.LANCZOS), ((i % 2) * 960, (i // 2) * 540))
    sheet = sheet.crop((0, 0, 1920, 540 * ((len(cards) + 1) // 2)))
    sheet.save(os.path.join(HERE, "overview.png"))


main()
