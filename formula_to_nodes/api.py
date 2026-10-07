# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender-side API for MCP clients (Claude Desktop, Claude Code, ...).

The MCP server (mcp_server.py) sends small Python snippets through Blender
Lab's MCP bridge add-on; those snippets call the functions below. Every
function returns a JSON-serializable dict.
"""

import bpy

from . import build, caps, compiler, decompile, ui
from .lang import FormulaError


def _err(message, **extra):
    return {"ok": False, "error": message, **extra}


def _params(tree):
    out = []
    for it in tree.interface.items_tree:
        if it.item_type != "SOCKET" or it.in_out != "INPUT" or it.socket_type == "NodeSocketGeometry":
            continue
        d = getattr(it, "default_value", None)
        if d is not None and not isinstance(d, (int, float, bool, str)):
            d = [round(x, 5) for x in d]
        out.append({"name": it.name, "type": it.socket_type.replace("NodeSocket", "").lower(), "default": d})
    return out


def _object(name):
    if not name:
        return None
    if name.lower() in ("active", "@active", "selected"):
        return bpy.context.view_layer.objects.active
    return bpy.data.objects.get(name)


def _redraw():
    try:
        ui.tag_redraw_node_editors()
    except Exception:
        pass


def _undo_push(message):
    try:
        bpy.ops.ed.undo_push(message=message)
    except Exception:
        pass


def build_group(script, name="Formula", update_group="", object_name="", mode="SCRIPT"):
    try:
        result = compiler.compile_source(script, mode, target=caps.from_blender(bpy))
    except FormulaError as e:
        return _err(str(e), line=e.line)

    target = None
    if update_group:
        target = bpy.data.node_groups.get(update_group)
        if target is not None and not build.is_formula_group(target):
            return _err(f"'{update_group}' wasn't made by Formula to Nodes — it won't be overwritten. "
                        f"Use another name.")
        if target is not None and target.library is not None:
            return _err(f"'{update_group}' is linked from a library and can't be edited")
    try:
        if target is not None:
            tree = build.build_group(result, script, mode, target=target, version=ui.VERSION)
        else:
            tree = build.build_group(result, script, mode, name=update_group or name or "Formula",
                                     version=ui.VERSION)
    except build.BuildError as e:
        return _err(f"build failed: {e}")

    info = {"ok": True, "group": tree.name, "updated": target is not None,
            "nodes": len(tree.nodes), "parameters": _params(tree), "notes": list(result.notes)}

    if object_name:
        ob = _object(object_name)
        if ob is None:
            info["warning"] = f"object '{object_name}' not found — the group was built but not applied"
        elif not any(s.key == "out:Geometry" for s in result.iface):
            info["warning"] = ("this script has no attribute writes (only a return value), so it can't "
                               "be a modifier — it was built as a field group")
        else:
            mod = next((m for m in ob.modifiers if m.type == "NODES" and m.node_group == tree), None)
            if mod is None:
                try:
                    mod = ob.modifiers.new(tree.name, "NODES")
                except (RuntimeError, TypeError) as e:
                    info["warning"] = f"couldn't add a modifier to '{ob.name}': {e}"
                    mod = None
                if mod is not None:
                    mod.node_group = tree
            if mod is not None:
                info["object"], info["modifier"] = ob.name, mod.name
    _undo_push(f"Formula: {tree.name}")
    _redraw()
    return info


def list_groups():
    groups = []
    for tree in bpy.data.node_groups:
        if not build.is_formula_group(tree):
            continue
        src = tree[build.SOURCE_KEY]
        users = [f"{ob.name} › {m.name}" for ob in bpy.data.objects for m in ob.modifiers
                 if m.type == "NODES" and m.node_group == tree]
        groups.append({"name": tree.name, "mode": tree.get(build.MODE_KEY, "SCRIPT"),
                       "first_line": src.strip().splitlines()[0][:100] if src.strip() else "",
                       "parameters": _params(tree), "used_by": users})
    return {"ok": True, "groups": groups}


def group_source(group):
    tree = bpy.data.node_groups.get(group)
    if tree is None:
        return _err(f"no node group named '{group}'")
    if not build.is_formula_group(tree):
        return _err(f"'{group}' wasn't made by Formula to Nodes, so it has no script")
    return {"ok": True, "group": tree.name, "mode": tree.get(build.MODE_KEY, "SCRIPT"),
            "script": tree[build.SOURCE_KEY], "parameters": _params(tree)}


def set_parameters(object_name, values, modifier=""):
    ob = _object(object_name)
    if ob is None:
        return _err(f"object '{object_name}' not found")
    mods = [m for m in ob.modifiers if m.type == "NODES" and m.node_group is not None
            and (m.name == modifier if modifier else build.is_formula_group(m.node_group))]
    if not mods:
        return _err(f"'{ob.name}' has no Formula to Nodes modifier" + (f" named '{modifier}'" if modifier else ""))
    mod = mods[-1]
    ids = {it.name: it.identifier for it in mod.node_group.interface.items_tree
           if it.item_type == "SOCKET" and it.in_out == "INPUT"}
    inputs = mod.properties.inputs
    done, missing = {}, []
    for name, value in values.items():
        ident = ids.get(name)
        item = getattr(inputs, ident, None) if ident else None
        if item is None:
            missing.append(name)
            continue
        try:
            item.value = value
            done[name] = value
        except (TypeError, ValueError) as e:
            missing.append(f"{name} ({e})")
    ob.update_tag()
    _undo_push("Formula: set parameters")
    _redraw()
    out = {"ok": not missing, "object": ob.name, "modifier": mod.name, "set": done}
    if missing:
        out["error"] = "couldn't set: " + ", ".join(missing) + f" (available: {', '.join(ids)})"
    return out


def _value(d):
    for attr in ("vector", "color", "value"):
        if hasattr(d, attr):
            v = getattr(d, attr)
            if isinstance(v, float):
                return round(v, 5)
            if isinstance(v, (int, bool, str)):
                return v
            return [round(x, 5) for x in v]
    return None


def inspect(object_name, attributes=None, limit=8):
    """Evaluated attribute values after all modifiers — lets the AI verify results."""
    ob = _object(object_name)
    if ob is None:
        return _err(f"object '{object_name}' not found")
    limit = max(1, min(int(limit), 200))
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    try:
        geo = ev.evaluated_geometry()
    except AttributeError:
        return _err("this Blender version can't read evaluated geometry")
    comps = {}
    for comp_name in ("mesh", "pointcloud", "curves"):
        comp = getattr(geo, comp_name, None)
        if comp is None:
            continue
        attrs = {}
        for a in comp.attributes:
            if a.name.startswith("."):
                continue
            if attributes and a.name not in attributes:
                continue
            data = a.data
            attrs[a.name] = {"domain": a.domain, "type": a.data_type, "count": len(data),
                             "values": [_value(data[i]) for i in range(min(limit, len(data)))]}
        comps[comp_name] = attrs
    warnings = []
    for m in ob.modifiers:
        for w in getattr(m, "node_warnings", []):
            warnings.append(f"{m.name}: {getattr(w, 'message', str(w))}")
    return {"ok": True, "object": ob.name, "components": comps, "warnings": warnings}


def scene_overview():
    objs = []
    active = bpy.context.view_layer.objects.active
    for ob in bpy.context.scene.objects:
        objs.append({
            "name": ob.name, "type": ob.type, "selected": ob.select_get(), "active": ob == active,
            "modifiers": [{"name": m.name, "type": m.type,
                           "node_group": m.node_group.name if m.type == "NODES" and m.node_group else None}
                          for m in ob.modifiers]})
    return {"ok": True, "blender": bpy.app.version_string, "file": bpy.data.filepath or "(unsaved)",
            "frame": bpy.context.scene.frame_current, "objects": objs,
            "formula_groups": [g["name"] for g in list_groups()["groups"]]}


def decompile_group(group):
    """Any Geometry Nodes group → a Formula to Nodes script (with notes on what didn't convert)."""
    tree = bpy.data.node_groups.get(group)
    if tree is None:
        return _err(f"no node group named '{group}'")
    if tree.bl_idname != "GeometryNodeTree":
        return _err(f"'{group}' isn't a Geometry Nodes group")
    try:
        script, notes = decompile.decompile(decompile.extract(tree))
    except Exception as e:      # an unusual tree shouldn't take the bridge down
        return _err(f"couldn't convert '{group}': {e}")
    return {"ok": True, "group": tree.name, "script": script, "notes": notes}


def capabilities():
    t = caps.from_blender(bpy)
    return {"ok": True, "blender": caps.version_str(t.version),
            "features": [{"feature": k, "available": ok, "detail": text} for k, ok, text in caps.report(t)]}
