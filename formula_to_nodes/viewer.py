# SPDX-License-Identifier: GPL-3.0-or-later
"""Live viewer: a node-shaped card drawn in the Geometry Node Editor while you write.

It recompiles the script as you type (only when the text changes) and shows the node the
script will become — its outputs, its inputs with defaults and panels, how many nodes it
builds — or the error with the offending line. Below it, live values: min / mean / max of
every attribute the script writes, read from the active object's evaluated geometry, like
glancing at Houdini's geometry spreadsheet. Nothing here touches the scene; it only draws.
"""

import hashlib
import math
import time

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from . import compiler
from .lang import FormulaError

# socket colours as Blender draws them
SOCKET_RGBA = {
    "FLOAT": (0.63, 0.63, 0.63, 1), "INT": (0.35, 0.55, 0.36, 1), "BOOL": (0.80, 0.65, 0.84, 1),
    "VECTOR": (0.39, 0.39, 0.78, 1), "COLOR": (0.78, 0.78, 0.16, 1), "ROTATION": (0.65, 0.39, 0.78, 1),
    "MATRIX": (0.72, 0.20, 0.52, 1), "STRING": (0.44, 0.70, 1.0, 1), "GEOMETRY": (0.0, 0.84, 0.64, 1),
    "OBJECT": (0.93, 0.62, 0.36, 1), "COLLECTION": (0.96, 0.96, 0.96, 1), "MATERIAL": (0.92, 0.46, 0.51, 1),
    "IMAGE": (0.39, 0.22, 0.39, 1), "SOUND": (0.55, 0.55, 0.45, 1),
}
TEXT = (0.92, 0.92, 0.92, 1)
MUTED = (0.62, 0.63, 0.66, 1)
OK = (0.42, 0.80, 0.50, 1)
WARN = (1.0, 0.72, 0.30, 1)
ERR = (1.0, 0.42, 0.40, 1)

ATTR_FIELD = {"FLOAT": ("value", 1), "INT": ("value", 1), "INT8": ("value", 1), "BOOLEAN": ("value", 1),
              "FLOAT_VECTOR": ("vector", 3), "FLOAT2": ("vector", 2), "INT32_2D": ("value", 2),
              "FLOAT_COLOR": ("color", 4), "BYTE_COLOR": ("color", 4), "QUATERNION": ("value", 4)}
MAX_ROWS = 10            # inputs shown before "+N more"
MAX_ATTRS = 8
SAMPLE_LIMIT = 2_000_000  # elements read per attribute before striding

_state = {
    "hash": None, "result": None, "error": None, "lines": [], "mode": "SCRIPT", "compiled_at": 0.0,
    "ms": 0.0, "stats": None, "stats_dirty": True, "stats_at": 0.0,
}
_handle = None
_shader = None


# ─────────────────────────────────────────────────────────────────────────────
#  Live compile + live values (timer; never during draw)
# ─────────────────────────────────────────────────────────────────────────────

def _settings():
    wm = bpy.context.window_manager
    win = wm.windows[0] if wm and wm.windows else None
    scene = win.scene if win else bpy.context.scene
    s = getattr(scene, "formula_settings", None) if scene else None
    return win, scene, s


def _viewer_visible(wm):
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type == "NODE_EDITOR" and area.spaces.active.tree_type == "GeometryNodeTree":
                return True
    return False


def current_source(s):
    """(source, mode) the viewer previews: the script if there is one, else the one-line formula."""
    from . import ui
    src = ui.script_text(s)
    if src.strip():
        return src, "SCRIPT"
    return s.formula, "FORMULA"


def _compile(s):
    from . import ui
    src, mode = current_source(s)
    key = hashlib.md5((mode + "\0" + s.output_type + "\0" + src).encode("utf-8", "replace")).hexdigest()
    if key == _state["hash"]:
        return False
    _state.update(hash=key, lines=src.split("\n"), mode=mode)
    untouched = mode == "FORMULA" and src == s.bl_rna.properties["formula"].default
    _state["idle"] = untouched
    if untouched:          # nothing written yet: show a hint instead of the sample formula
        _state.update(result=None, error=None)
        return True
    t = time.perf_counter()
    try:
        res = compiler.compile_source(src, mode, s.output_type if mode == "FORMULA" else "AUTO",
                                      **ui.compile_options())
        _state.update(result=res, error=None)
    except FormulaError as e:
        _state.update(result=None, error=(str(e.message if hasattr(e, "message") else e), e.line))
    except Exception as e:  # a compiler bug must never break the editor
        _state.update(result=None, error=(f"internal error: {e}", None))
    _state["ms"] = (time.perf_counter() - t) * 1000
    _state["compiled_at"] = time.time()
    _state["stats_dirty"] = True
    return True


def _components(gs):
    for label in ("mesh", "pointcloud", "curves"):
        comp = getattr(gs, label, None)
        if comp is not None:
            yield label, comp
    inst = getattr(gs, "instances_pointcloud", None)
    if callable(inst):
        try:
            pc = inst()
        except Exception:
            pc = None
        if pc is not None:
            yield "instances", pc


def _attr_stats(attr, n):
    import numpy as np
    field, width = ATTR_FIELD.get(attr.data_type, (None, 0))
    if field is None or n == 0:
        return None
    buf = np.empty(n * width, dtype=np.float64 if attr.data_type != "BOOLEAN" else bool)
    try:
        attr.data.foreach_get(field, buf)
    except Exception:
        return None
    arr = buf.reshape(n, width).astype(np.float64)
    if n > SAMPLE_LIMIT:
        arr = arr[:: max(1, n // SAMPLE_LIMIT)]
    if not np.all(np.isfinite(arr)):
        bad = int(np.count_nonzero(~np.isfinite(arr).all(axis=1)))
        arr = arr[np.isfinite(arr).all(axis=1)]
        if arr.size == 0:
            return {"width": width, "bad": bad}
    else:
        bad = 0
    return {"width": width, "min": arr.min(axis=0).tolist(), "mean": arr.mean(axis=0).tolist(),
            "max": arr.max(axis=0).tolist(), "bad": bad}


def _read_stats(win):
    """Values of the attributes the script writes, on the active object's evaluated geometry."""
    vl = win.view_layer
    ob = vl.objects.active if vl else None
    if ob is None:
        return {"object": None}
    out = {"object": ob.name, "frame": win.scene.frame_current, "counts": [], "attrs": [], "position": None}
    try:
        if isinstance(win, bpy.types.Window):
            with bpy.context.temp_override(window=win):
                dg = bpy.context.evaluated_depsgraph_get()
        else:
            dg = bpy.context.evaluated_depsgraph_get()
        gs = ob.evaluated_get(dg).evaluated_geometry()
    except Exception as e:
        out["error"] = str(e)
        return out
    res = _state["result"]
    wanted = list(res.attrs) if res is not None else []
    for label, comp in _components(gs):
        try:
            attrs = comp.attributes
        except Exception:
            continue
        pos = attrs.get("position")
        n = len(pos.data) if pos is not None else 0
        if label == "mesh":
            out["counts"].append(f"{len(comp.vertices):,} points · {len(comp.polygons):,} faces")
        elif n:
            out["counts"].append(f"{n:,} {'instances' if label == 'instances' else 'points' if label == 'pointcloud' else 'curve points'}")
        if pos is not None and out["position"] is None and n:
            st = _attr_stats(pos, n)
            if st:
                out["position"] = st
        for name in wanted:
            if len(out["attrs"]) >= MAX_ATTRS or any(a["name"] == name for a in out["attrs"]):
                continue
            a = attrs.get(name)
            if a is None:
                continue
            count = len(a.data)
            st = _attr_stats(a, count)
            if st is not None:
                st.update(name=name, domain=a.domain.lower(), type=a.data_type, count=count)
                out["attrs"].append(st)
    missing = [n for n in wanted if not any(a["name"] == n for a in out["attrs"])]
    out["missing"] = missing[:4]
    del gs
    return out


def _tick():
    try:
        wm = bpy.context.window_manager
        win, scene, s = _settings()
        if s is None or not s.show_viewer or not _viewer_visible(wm):
            return 0.5
        changed = _compile(s)
        now = time.time()
        if _state["stats_dirty"] and now - _state["stats_at"] > 0.25:
            _state["stats"] = _read_stats(win) if s.viewer_values else None
            _state["stats_dirty"] = False
            _state["stats_at"] = now
            changed = True
        if changed:
            for w in wm.windows:
                for area in w.screen.areas:
                    if area.type == "NODE_EDITOR":
                        area.tag_redraw()
    except Exception:
        pass
    return 0.3


def _mark_dirty(*_args):
    _state["stats_dirty"] = True


def invalidate():
    """Force a recompile + refresh on the next tick (e.g. after loading a recipe)."""
    _state["hash"] = None
    _state["stats_dirty"] = True


# ─────────────────────────────────────────────────────────────────────────────
#  Drawing
# ─────────────────────────────────────────────────────────────────────────────

def _sh():
    global _shader
    if _shader is None:
        _shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    return _shader


def _rect(x, y, w, h, color, r=0.0):
    """Filled (rounded) rectangle, bottom-left at x, y."""
    if w <= 0 or h <= 0:
        return
    r = max(0.0, min(r, w / 2, h / 2))
    if r < 0.5:
        verts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    else:
        verts = []
        for cx, cy, a0 in ((x + w - r, y + r, -90), (x + w - r, y + h - r, 0), (x + r, y + h - r, 90),
                           (x + r, y + r, 180)):
            for i in range(7):
                a = math.radians(a0 + i * 15)
                verts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    cx, cy = x + w / 2, y + h / 2
    tris = []
    for i in range(len(verts)):
        tris += [(cx, cy), verts[i], verts[(i + 1) % len(verts)]]
    sh = _sh()
    batch = batch_for_shader(sh, "TRIS", {"pos": tris})
    sh.bind()
    sh.uniform_float("color", color)
    batch.draw(sh)


def _dot(x, y, r, color):
    _rect(x - r, y - r, 2 * r, 2 * r, (0.08, 0.08, 0.08, 1), r)
    _rect(x - r + 1, y - r + 1, 2 * r - 2, 2 * r - 2, color, r - 1)


def _text(s, x, y, size, color, right=False, clip=None):
    blf.size(0, size)
    if clip is not None:
        s = _elide(s, clip, size)
    if right:
        x -= blf.dimensions(0, s)[0]
    blf.color(0, *color)
    blf.position(0, x, y, 0)
    blf.draw(0, s)


def _tw(s, size):
    blf.size(0, size)
    return blf.dimensions(0, s)[0]


def _elide(s, width, size):
    if _tw(s, size) <= width:
        return s
    while s and _tw(s + "…", size) > width:
        s = s[:-1]
    return s + "…"


def _wrap(s, width, size, max_lines=4):
    words, lines, line = s.split(), [], ""
    for w in words:
        trial = f"{line} {w}".strip()
        if _tw(trial, size) > width and line:
            lines.append(line)
            line = w
            if len(lines) == max_lines:
                break
        else:
            line = trial
    if line and len(lines) < max_lines:
        lines.append(line)
    return lines


def _fmt_num(v):
    if isinstance(v, bool):
        return "on" if v else "off"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != 0 and (abs(v) >= 1e5 or abs(v) < 1e-3):
            return f"{v:.2e}"
        return f"{v:.3f}".rstrip("0").rstrip(".") if "." in f"{v:.3f}" else f"{v:.3f}"
    return str(v)


def fmt_default(sock):
    v = sock.default
    if v is None:
        return {"OBJECT": "pick…", "COLLECTION": "pick…", "MATERIAL": "pick…", "IMAGE": "pick…",
                "GEOMETRY": ""}.get(sock.vtype, "")
    if isinstance(v, (tuple, list)):
        if v and isinstance(v[0], str):
            return ""
        return "(" + ", ".join(_fmt_num(float(c)) for c in v[:4]) + ")"
    if isinstance(v, str):
        return f'"{v}"'
    return _fmt_num(v)


def _fmt_stat(st, key):
    vals = st[key]
    if st["width"] == 1:
        return _fmt_num(vals[0])
    return "(" + ", ".join(f"{c:.2f}" for c in vals[:st["width"]]) + ")"


def _right_edge(region, area):
    """x where the card should end: left of an overlapping sidebar."""
    right = region.width
    for r in area.regions:
        if r.type == "UI" and r.width > 1 and region.x < r.x < region.x + region.width:
            right = min(right, r.x - region.x)
    return right


def _draw():
    try:
        context = bpy.context
        space = context.space_data
        if space is None or space.tree_type != "GeometryNodeTree":
            return
        s = getattr(context.scene, "formula_settings", None)
        if s is None or not s.show_viewer or _state["hash"] is None:
            return
        _draw_card(context, s)
    except Exception:
        pass


def _draw_card(context, s):
    region, area = context.region, context.area
    ui = context.preferences.system.ui_scale
    W = 270 * ui
    pad = 10 * ui
    row = 22 * ui
    fs, fsm = 12 * ui, 10.5 * ui
    res, err = _state["result"], _state["error"]
    gpu.state.blend_set("ALPHA")
    if _state.get("idle"):
        _draw_hint(context, s, ui, W, pad, fs, fsm)
        gpu.state.blend_set("NONE")
        return

    # ── content, measured first (y grows downward from the top) ──
    items = []                        # (kind, payload, height)
    if res is not None:
        outs = [i for i in res.iface if i.in_out == "OUTPUT"]
        ins = [i for i in res.iface if i.in_out == "INPUT"]
        for o in outs:
            items.append(("out", o, row))
        items.append(("gap", None, 6 * ui))
        panel = None
        shown = 0
        for i in ins:
            if shown >= MAX_ROWS:
                items.append(("more", len(ins) - shown, row * 0.8))
                break
            if i.panel and i.panel != panel:
                items.append(("panel", i.panel, row * 0.8))
                panel = i.panel
            items.append(("in", i, row))
            shown += 1
        for n in res.notes[:3]:
            lines = _wrap(n, W - 2 * pad - 14 * ui, fsm, 3)
            items.append(("note", lines, len(lines) * 14 * ui + 6 * ui))
        if len(res.notes) > 3:
            items.append(("note", [f"+{len(res.notes) - 3} more notes"], 20 * ui))
    elif err is not None:
        msg, line = err
        lines = _wrap(msg, W - 2 * pad, fsm, 5)
        src = ""
        if line and 0 < line <= len(_state["lines"]):
            src = _state["lines"][line - 1].strip()
        items.append(("error", (lines, src, line), (len(lines) + (2 if src else 0)) * 15 * ui + 12 * ui))

    stats = _state["stats"] if s.viewer_values else None
    stat_items = []
    if stats and stats.get("object"):
        stat_items.append(("shead", stats, row))
        for c in stats.get("counts", [])[:2]:
            stat_items.append(("scount", c, 16 * ui))
        if stats.get("error"):
            stat_items.append(("smiss", stats["error"][:60], 16 * ui))
        for a in stats.get("attrs", []):
            stat_items.append(("sattr", a, (2 if a["width"] == 1 else 3) * 15 * ui + 4 * ui))
        if stats.get("missing") and res is not None:
            stat_items.append(("smiss", "not on this object yet: " + ", ".join(stats["missing"]), 30 * ui))
        if not stats.get("attrs") and stats.get("position"):
            p = stats["position"]
            stat_items.append(("spos", p, 2 * 15 * ui + 4 * ui))

    header = 28 * ui
    body = sum(h for _k, _p, h in items) + pad
    sbody = (sum(h for _k, _p, h in stat_items) + pad) if stat_items else 0
    H = header + body + sbody

    right = _right_edge(region, area) - 14 * ui
    x = right - W
    if s.viewer_corner == "LEFT":
        x = 14 * ui
    top = region.height - 14 * ui
    y0 = top - H
    if y0 < 4:
        y0 = 4
        top = y0 + H

    theme = context.preferences.themes[0].node_editor
    head_col = tuple(theme.group_node)[:3] + (1.0,)
    _rect(x - 2, y0 - 3, W + 4, H + 4, (0, 0, 0, 0.35), 7 * ui)
    _rect(x, y0, W, H, (0.16, 0.165, 0.18, 0.97), 6 * ui)
    _rect(x, top - header, W, header, head_col, 6 * ui)
    _rect(x, top - header, W, 6 * ui, head_col)

    # header: name + status chip
    from . import ui as _ui
    target = _ui.target_tree(context.space_data) if s.replace_active else None
    title = target.name if target else (s.group_name or "Formula")
    if res is not None:
        chip, ccol = f"✓ {res.node_count} nodes", OK
    elif err is not None:
        chip, ccol = (f"✗ line {err[1]}" if err[1] else "✗ error"), ERR
    else:
        chip, ccol = "…", MUTED
    cw = _tw(chip, fsm) + 12 * ui
    _rect(x + W - pad - cw, top - header + 6 * ui, cw, header - 12 * ui, (0, 0, 0, 0.28), 4 * ui)
    _text(chip, x + W - pad - 6 * ui, top - header + 10 * ui, fsm, ccol, right=True)
    _text(("✎ " if target else "+ ") + title, x + pad, top - header + 9 * ui, fs, TEXT,
          clip=W - 3 * pad - cw)

    # body
    y = top - header - 4 * ui
    sock_r = 4.5 * ui
    for kind, p, h in items:
        mid = y - h / 2
        if kind == "out":
            _text(p.name, x + W - pad - 4 * ui, mid - 4 * ui, fs, TEXT, right=True)
            _dot(x + W, mid, sock_r, SOCKET_RGBA.get(p.vtype, MUTED))
        elif kind == "in":
            _dot(x, mid, sock_r, SOCKET_RGBA.get(p.vtype, MUTED))
            val = fmt_default(p)
            if val:
                vw = min(_tw(val, fsm) + 12 * ui, W * 0.48)
                _rect(x + W - pad - vw, mid - 8 * ui, vw, 16 * ui, (0.235, 0.25, 0.27, 1), 3 * ui)
                _text(val, x + W - pad - 6 * ui, mid - 4 * ui, fsm, TEXT, right=True, clip=vw - 10 * ui)
                avail = W - 2 * pad - vw - 6 * ui
            else:
                avail = W - 2 * pad
            _text(p.name, x + pad, mid - 4 * ui, fs, TEXT, clip=avail)
        elif kind == "panel":
            _text("▾ " + p, x + pad, mid - 4 * ui, fsm, MUTED)
        elif kind == "more":
            _text(f"+{p} more inputs", x + pad, mid - 4 * ui, fsm, MUTED)
        elif kind == "note":
            yy = y - 14 * ui
            _text("!", x + pad, yy, fsm, WARN)
            for ln in p:
                _text(ln, x + pad + 12 * ui, yy, fsm, WARN)
                yy -= 14 * ui
        elif kind == "error":
            lines, src, line = p
            _rect(x + 6 * ui, y - h + 4 * ui, W - 12 * ui, h - 6 * ui, (0.35, 0.10, 0.10, 0.55), 4 * ui)
            yy = y - 16 * ui
            for ln in lines:
                _text(ln, x + pad, yy, fsm, ERR)
                yy -= 15 * ui
            if src:
                yy -= 4 * ui
                _text(f"{line:>3} │ {src}", x + pad, yy, fsm, TEXT, clip=W - 2 * pad)
        y -= h

    if stat_items:
        y -= pad / 2
        _rect(x + pad, y, W - 2 * pad, 1, (0.4, 0.42, 0.45, 0.7))
        for kind, p, h in stat_items:
            if kind == "shead":
                _text(f"Values on {p['object']} · frame {p.get('frame', '')}", x + pad, y - 16 * ui, fsm, MUTED,
                      clip=W - 2 * pad)
            elif kind == "scount":
                _text(p, x + pad, y - 12 * ui, fsm, MUTED)
            elif kind == "smiss":
                for i, ln in enumerate(_wrap(p, W - 2 * pad, fsm, 2)):
                    _text(ln, x + pad, y - 12 * ui - i * 14 * ui, fsm, WARN)
            elif kind in ("sattr", "spos"):
                name = p.get("name", "P")
                vt = {"FLOAT": "FLOAT", "INT": "INT", "BOOLEAN": "BOOL", "FLOAT_COLOR": "COLOR",
                      "BYTE_COLOR": "COLOR", "QUATERNION": "ROTATION"}.get(p.get("type", "FLOAT_VECTOR"), "VECTOR")
                _dot(x + pad + 4 * ui, y - 9 * ui, 3.5 * ui, SOCKET_RGBA.get(vt, MUTED))
                head = f"{name}" + (f"  ({p['domain']}, {p['count']:,})" if "domain" in p else "  (position)")
                if p.get("bad"):
                    head += f"  ⚠ {p['bad']} NaN/inf"
                _text(head, x + pad + 14 * ui, y - 13 * ui, fsm, ERR if p.get("bad") else TEXT, clip=W - 3 * pad)
                if "min" in p:
                    if p["width"] == 1:
                        _text(f"min {_fmt_stat(p, 'min')}   avg {_fmt_stat(p, 'mean')}   max {_fmt_stat(p, 'max')}",
                              x + pad + 14 * ui, y - 28 * ui, fsm, MUTED, clip=W - 3 * pad)
                    else:
                        _text(f"min {_fmt_stat(p, 'min')}", x + pad + 14 * ui, y - 28 * ui, fsm, MUTED,
                              clip=W - 3 * pad)
                        _text(f"max {_fmt_stat(p, 'max')}", x + pad + 14 * ui, y - 43 * ui, fsm, MUTED,
                              clip=W - 3 * pad)
            y -= h
    gpu.state.blend_set("NONE")


# ─────────────────────────────────────────────────────────────────────────────

def _draw_hint(context, s, ui, W, pad, fs, fsm):
    region, area = context.region, context.area
    lines = _wrap("Pick a recipe in the sidebar (N › Formula), or write a script — the node it becomes "
                  "appears here as you type.", W - 2 * pad, fsm, 4)
    header = 28 * ui
    H = header + len(lines) * 15 * ui + pad * 1.5
    x = (_right_edge(region, area) - 14 * ui - W) if s.viewer_corner == "RIGHT" else 14 * ui
    top = region.height - 14 * ui
    y0 = top - H
    theme = context.preferences.themes[0].node_editor
    head_col = tuple(theme.group_node)[:3] + (0.85,)
    _rect(x, y0, W, H, (0.16, 0.165, 0.18, 0.85), 6 * ui)
    _rect(x, top - header, W, header, head_col, 6 * ui)
    _rect(x, top - header, W, 6 * ui, head_col)
    _text("Formula viewer", x + pad, top - header + 9 * ui, fs, TEXT)
    y = top - header - 16 * ui
    for ln in lines:
        _text(ln, x + pad, y, fsm, MUTED)
        y -= 15 * ui


_NS_KEY = "formula_to_nodes_viewer"


def _forget_previous():
    """A reload (or a failed one) must never leave an old copy drawing: the live handle and the
    state of whichever module registered it are kept in one shared place and retired here."""
    prev = bpy.app.driver_namespace.get(_NS_KEY)
    if not prev:
        return
    handle, state, tick = prev
    try:
        bpy.types.SpaceNodeEditor.draw_handler_remove(handle, "WINDOW")
    except (ValueError, TypeError, RuntimeError):
        pass
    state["hash"] = None
    state["result"] = None
    if tick is not None and bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)
    bpy.app.driver_namespace.pop(_NS_KEY, None)


def register():
    global _handle
    _forget_previous()
    if _handle is None:
        _handle = bpy.types.SpaceNodeEditor.draw_handler_add(_draw, (), "WINDOW", "POST_PIXEL")
        bpy.app.driver_namespace[_NS_KEY] = (_handle, _state, _tick)
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.5, persistent=True)
    for hs in (bpy.app.handlers.depsgraph_update_post, bpy.app.handlers.frame_change_post):
        if _mark_dirty not in hs:
            hs.append(_mark_dirty)
    invalidate()


def unregister():
    global _handle
    if _handle is not None:
        try:
            bpy.types.SpaceNodeEditor.draw_handler_remove(_handle, "WINDOW")
        except (ValueError, TypeError, RuntimeError):
            pass
        _handle = None
    if bpy.app.driver_namespace.get(_NS_KEY, (None,))[0] is not None:
        bpy.app.driver_namespace.pop(_NS_KEY, None)
    _state["hash"] = None
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    for hs in (bpy.app.handlers.depsgraph_update_post, bpy.app.handlers.frame_change_post):
        while _mark_dirty in hs:
            hs.remove(_mark_dirty)
