# SPDX-License-Identifier: GPL-3.0-or-later
"""chramp() / colorramp(): preset shapes, and keeping the shapes people edit
in the sidebar when a group is rebuilt."""

import colorsys

RAMP_KEY = "ftn_ramp"
PRESET_KEY = "ftn_ramp_preset"
KIND_KEY = "ftn_ramp_kind"

A, C, V = "AUTO", "AUTO_CLAMPED", "VECTOR"

# float curves: [(x, y, handle)]
FLOAT_PRESETS = {
    "linear":   [(0.0, 0.0, A), (1.0, 1.0, A)],
    "smooth":   [(0.0, 0.0, C), (1.0, 1.0, C)],
    "ease_in":  [(0.0, 0.0, C), (0.65, 0.2, A), (1.0, 1.0, C)],
    "ease_out": [(0.0, 0.0, C), (0.35, 0.8, A), (1.0, 1.0, C)],
    "bell":     [(0.0, 0.0, C), (0.5, 1.0, C), (1.0, 0.0, C)],
    "valley":   [(0.0, 1.0, C), (0.5, 0.0, C), (1.0, 1.0, C)],
    "spike":    [(0.0, 0.0, V), (0.5, 1.0, V), (1.0, 0.0, V)],
    "plateau":  [(0.0, 0.0, C), (0.25, 1.0, C), (0.75, 1.0, C), (1.0, 0.0, C)],
    "steps":    [(0.0, 0.0, V), (0.249, 0.0, V), (0.25, 0.333, V), (0.499, 0.333, V), (0.5, 0.667, V),
                 (0.749, 0.667, V), (0.75, 1.0, V), (1.0, 1.0, V)],
    "sine":     [(0.0, 0.5, A), (0.25, 1.0, C), (0.5, 0.5, A), (0.75, 0.0, C), (1.0, 0.5, A)],
    "invert":   [(0.0, 1.0, A), (1.0, 0.0, A)],
}


def _hsv(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return (r, g, b, 1.0)


# colour ramps: (interpolation, [(position, rgba)])
COLOR_PRESETS = {
    "grayscale": ("LINEAR", [(0.0, (0, 0, 0, 1)), (1.0, (1, 1, 1, 1))]),
    "fire": ("LINEAR", [(0.0, (0, 0, 0, 1)), (0.3, (0.6, 0.02, 0.0, 1)), (0.6, (1.0, 0.35, 0.0, 1)),
                        (0.85, (1.0, 0.85, 0.2, 1)), (1.0, (1, 1, 0.9, 1))]),
    "heat": ("LINEAR", [(0.0, (0.02, 0.02, 0.3, 1)), (0.25, (0.0, 0.4, 1.0, 1)), (0.5, (0.1, 0.9, 0.3, 1)),
                        (0.75, (1.0, 0.85, 0.0, 1)), (1.0, (0.9, 0.05, 0.05, 1))]),
    "rainbow": ("LINEAR", [(i / 6.0, _hsv(i / 6.0 * 0.83, 0.9, 1.0)) for i in range(7)]),
    "viridis": ("LINEAR", [(0.0, (0.267, 0.005, 0.329, 1)), (0.25, (0.229, 0.322, 0.546, 1)),
                           (0.5, (0.128, 0.567, 0.551, 1)), (0.75, (0.369, 0.789, 0.383, 1)),
                           (1.0, (0.993, 0.906, 0.144, 1))]),
    "magma": ("LINEAR", [(0.0, (0.001, 0.0, 0.014, 1)), (0.25, (0.317, 0.071, 0.485, 1)),
                         (0.5, (0.716, 0.215, 0.475, 1)), (0.75, (0.987, 0.535, 0.382, 1)),
                         (1.0, (0.987, 0.991, 0.75, 1))]),
    "terrain": ("LINEAR", [(0.0, (0.05, 0.15, 0.45, 1)), (0.3, (0.1, 0.45, 0.2, 1)), (0.6, (0.4, 0.33, 0.2, 1)),
                           (0.8, (0.55, 0.5, 0.45, 1)), (1.0, (0.95, 0.95, 0.97, 1))]),
    "ice": ("EASE", [(0.0, (0.02, 0.05, 0.15, 1)), (0.5, (0.25, 0.6, 0.85, 1)), (1.0, (0.92, 0.97, 1.0, 1))]),
}


def save_all(tree):
    """{(kind, name, occurrence): (preset, data)} for every ramp node in tree."""
    out, seen = {}, {}
    for node in tree.nodes:
        name = node.get(RAMP_KEY) if hasattr(node, "get") else None
        if name is None:
            continue
        kind = node.get(KIND_KEY, "float")
        occ = seen.get((kind, name), 0)
        seen[(kind, name)] = occ + 1
        try:
            out[(kind, name, occ)] = (node.get(PRESET_KEY, ""), _read(node, kind))
        except Exception:
            pass
    return out


def _read(node, kind):
    if kind == "color":
        cr = node.color_ramp
        return (cr.interpolation, [(e.position, tuple(e.color)) for e in cr.elements])
    curve = node.mapping.curves[0]
    return [(p.location[0], p.location[1], p.handle_type) for p in curve.points]


def apply(node, kind, name, preset, saved=None):
    """Shape a new ramp node: keep the saved (user edited) shape if the
    script still asks for the same preset, else apply the preset."""
    node[RAMP_KEY] = name
    node[KIND_KEY] = kind
    node[PRESET_KEY] = preset
    data = None
    if saved is not None and saved[0] == preset:
        data = saved[1]
    if kind == "color":
        _write_color(node, data or COLOR_PRESETS.get(preset, COLOR_PRESETS["grayscale"]))
    else:
        _write_float(node, data or FLOAT_PRESETS.get(preset, FLOAT_PRESETS["linear"]))


def _write_float(node, points):
    curve = node.mapping.curves[0]
    pts = curve.points
    while len(pts) > max(len(points), 2):
        pts.remove(pts[len(pts) - 1])
    while len(pts) < len(points):
        pts.new(0.5, 0.5)
    for p, (x, y, h) in zip(sorted(pts, key=lambda q: q.location[0]), points):
        p.location = (x, y)
        p.handle_type = h
    node.mapping.update()


def _write_color(node, data):
    interp, elements = data
    cr = node.color_ramp
    cr.interpolation = interp
    els = cr.elements
    while len(els) > max(len(elements), 1):
        els.remove(els[len(els) - 1])
    while len(els) < len(elements):
        els.new(0.5)
    ordered = sorted(els, key=lambda e: e.position)
    for e, (pos, col) in zip(ordered, elements):
        e.position = pos
        e.color = tuple(col)


def ramp_nodes(tree):
    """[(name, kind, node)] for the sidebar."""
    out = []
    for node in tree.nodes:
        name = node.get(RAMP_KEY) if hasattr(node, "get") else None
        if name is not None:
            out.append((name, node.get(KIND_KEY, "float"), node))
    return out
