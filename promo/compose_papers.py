# SPDX-License-Identifier: GPL-3.0-or-later
"""Instagram carousel (1080×1350) + web banner (1920×1080) for the two paper implementations.

    python promo/compose_papers.py <folder with renders, node shots and paper_meta.json>

Every number on the cards comes from paper_meta.json, which is filled from the measured test
results (promo/paper_tests.py) and the sim stats (promo/sims.py)."""

import json
import os
import sys
import textwrap

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from compose_53 import BOLD, MONO, SANS, _font, draw_code, fit, rounded  # noqa: E402
from draw_nodemap import draw_nodemap  # noqa: E402

INK, PAPER, MUTED, LINE = (240, 241, 245), (9, 10, 13), (150, 156, 170), (40, 43, 52)


def chip(d, x, y, text, font, fg=(20, 20, 24), bg=(236, 236, 240)):
    w = d.textlength(text, font=font) + 30
    d.rounded_rectangle((x, y, x + w, y + 42), 21, fill=bg)
    d.text((x + 15, y + 8), text, font=font, fill=fg)
    return x + w + 12


def shade(img, top=0, bottom=520, h=None, strength=1.0):
    """Darken the bottom of an image so text sits on it."""
    h = h or img.height
    g = Image.new("L", (1, bottom))
    for y in range(bottom):
        g.putpixel((0, y), int(255 * strength * (y / (bottom - 1)) ** 1.4))
    band = Image.new("RGB", (img.width, bottom), PAPER)
    img.paste(band, (0, h - bottom), g.resize((img.width, bottom)))


def slide_hero(meta, render, out, idx):
    W, H = 1080, 1350
    img = fit(render, W, H)
    shade(img, bottom=640)
    d = ImageDraw.Draw(img)
    x = chip(d, 56, 56, meta["venue"], _font(BOLD, 20))
    chip(d, x, 56, "Blender 5.3 Geometry Nodes", _font(BOLD, 20), fg=INK, bg=(40, 44, 56))
    y = H - 430
    for line in textwrap.wrap(meta["headline"], 22):
        d.text((56, y), line, font=_font(BOLD, 66), fill=INK)
        y += 76
    d.text((58, y + 12), meta["paper"], font=_font(SANS, 24), fill=MUTED)
    d.text((58, y + 46), meta["authors"], font=_font(SANS, 22), fill=MUTED)
    d.text((58, H - 70), "Formula to Nodes 3.1  ·  swipe →", font=_font(BOLD, 22), fill=INK)
    p = os.path.join(out, f"{meta['key']}_{idx}_hero.png")
    img.save(p)
    return p


def slide_mapping(meta, render, out, idx):
    W, H = 1080, 1350
    img = Image.new("RGB", (W, H), PAPER)
    strip = fit(render, W, 330)
    img.paste(strip, (0, 0))
    shade(img, bottom=200, h=330, strength=1.0)
    d = ImageDraw.Draw(img)
    d.text((56, 270), "The paper, node by node", font=_font(BOLD, 48), fill=INK)
    y = 380
    for eq, node in meta["mapping"]:
        d.line((56, y - 18, W - 56, y - 18), fill=LINE, width=2)
        yy = y
        for line in textwrap.wrap(eq, 40):
            d.text((56, yy), line, font=_font(MONO, 23), fill=(255, 196, 120))
            yy += 32
        y2 = y
        for line in textwrap.wrap(node, 26):
            d.text((640, y2), line, font=_font(BOLD, 24), fill=INK)
            y2 += 32
        y = max(yy, y2) + 40
    d.text((56, H - 70), meta["mapping_note"], font=_font(SANS, 20), fill=MUTED)
    p = os.path.join(out, f"{meta['key']}_{idx}_nodes_map.png")
    img.save(p)
    return p


def slide_proof(meta, render, out, idx):
    W, H = 1080, 1350
    img = Image.new("RGB", (W, H), PAPER)
    strip = fit(render, W, 420)
    fade = Image.new("L", (1, 420))
    for y in range(420):
        fade.putpixel((0, y), int(255 * min(1, y / 260) ** 1.5))
    img.paste(strip, (0, H - 420), fade.resize((W, 420)))
    d = ImageDraw.Draw(img)
    d.text((56, 70), "Checked against the physics", font=_font(BOLD, 48), fill=INK)
    y = 190
    for big, label in meta["proof"]:
        d.text((56, y), big, font=_font(BOLD, 84), fill=(255, 196, 120))
        yy = y + 100
        for line in textwrap.wrap(label, 50):
            d.text((60, yy), line, font=_font(SANS, 26), fill=MUTED)
            yy += 36
        y = yy + 46
    d.rectangle((0, H - 64, W, H), fill=PAPER)
    d.text((56, H - 50), meta["proof_note"], font=_font(SANS, 19), fill=MUTED)
    p = os.path.join(out, f"{meta['key']}_{idx}_proof.png")
    img.save(p)
    return p


def slide_script(meta, nodes, out, idx):
    W, H = 1080, 1350
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    d.text((56, 60), "One script in, a node group out", font=_font(BOLD, 44), fill=INK)
    d.text((58, 120), f"{meta['lines']} lines of script  →  {meta['nodes']} Geometry Nodes", font=_font(SANS, 26),
           fill=MUTED)
    card = Image.new("RGB", (W - 112, 560), (20, 22, 28))
    draw_code(ImageDraw.Draw(card), 26, 22, meta["excerpt"], size=17, max_lines=25, lh=21)
    img.paste(rounded(card), (56, 180), rounded(card))
    if nodes is not None:
        n = fit(nodes, W - 112, 520, mode="contain") if nodes.width / nodes.height > 2.5 else fit(nodes, W - 112, 520)
        img.paste(rounded(n), (56, 770), rounded(n))
        d.text((58, H - 46), "The generated node group — Blender 5.3 nodes outlined", font=_font(SANS, 20),
               fill=MUTED)
    p = os.path.join(out, f"{meta['key']}_{idx}_script.png")
    img.save(p)
    return p


def web(meta, render, nodes, out):
    W, H = 1920, 1080
    img = Image.new("RGB", (W, H), PAPER)
    img.paste(fit(render, 900, H), (W - 900, 0))
    d = ImageDraw.Draw(img)
    x = chip(d, 70, 64, meta["venue"], _font(BOLD, 20))
    chip(d, x, 64, "Blender 5.3", _font(BOLD, 20), fg=INK, bg=(40, 44, 56))
    y = 140
    for line in textwrap.wrap(meta["headline"], 26):
        d.text((70, y), line, font=_font(BOLD, 58), fill=INK)
        y += 68
    d.text((72, y + 10), meta["paper"], font=_font(SANS, 22), fill=MUTED)
    y += 70
    for eq, node in meta["mapping"][:5]:
        d.text((72, y), "•", font=_font(BOLD, 24), fill=(255, 196, 120))
        d.text((100, y), node, font=_font(BOLD, 24), fill=INK)
        d.text((100, y + 32), eq, font=_font(MONO, 18), fill=MUTED)
        y += 78
    x = 72
    for (big, _), short in zip(meta["proof"][:3], meta["proof_short"]):
        d.text((x, H - 200), big, font=_font(BOLD, 34), fill=(255, 196, 120))
        d.text((x + 2, H - 152), short, font=_font(SANS, 19), fill=MUTED)
        x += 330
    d.text((72, H - 70), "Formula to Nodes 3.1 — type the method, get the node group", font=_font(SANS, 22),
           fill=MUTED)
    p = os.path.join(out, f"{meta['key']}_web.png")
    img.save(p)
    return p


W = 1080


def main(folder):
    metas = json.load(open(os.path.join(folder, "paper_meta.json")))
    out = os.path.join(folder, "paper_cards")
    os.makedirs(out, exist_ok=True)
    made = []
    for meta in metas:
        render = Image.open(os.path.join(folder, meta["render"])).convert("RGB")
        nodes = None
        if meta.get("nodes_shot") and os.path.exists(os.path.join(folder, meta["nodes_shot"])):
            nodes = Image.open(os.path.join(folder, meta["nodes_shot"])).convert("RGB")
        elif meta.get("nodes_map") and os.path.exists(os.path.join(folder, meta["nodes_map"])):
            nodes, nm = draw_nodemap(os.path.join(folder, meta["nodes_map"]), W - 112, 520,
                                     label_font=_font(BOLD, 17))
            meta["nodes"] = nm["count"]
            nodes.save(os.path.join(out, f"{meta['key']}_nodemap.png"))
        made += [slide_hero(meta, render, out, 1), slide_mapping(meta, render, out, 2),
                 slide_proof(meta, render, out, 3), slide_script(meta, nodes, out, 4), web(meta, render, nodes, out)]
    print("\n".join(made))


if __name__ == "__main__":
    main(sys.argv[1])
