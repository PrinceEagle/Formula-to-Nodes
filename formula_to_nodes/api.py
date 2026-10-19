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


def _panel_path(it):
    names, p = [], getattr(it, "parent", None)
    while p is not None and getattr(p, "name", ""):
        names.append(p.name)
        p = getattr(p, "parent", None)
    return "/".join(reversed(names))


def _params(tree):
    """Inputs in the order they appear on the modifier, with their panel path ("Scale/min")."""
    from . import ramps
    out = []
    for it in tree.interface.items_tree:
        if it.item_type != "SOCKET" or it.in_out != "INPUT" or it.socket_type == "NodeSocketGeometry":
            continue
        d = getattr(it, "default_value", None)
        if isinstance(d, float):
            d = round(d, 5)
        elif d is not None and not isinstance(d, (int, bool, str)):
            try:
                d = [round(x, 5) for x in d]
            except TypeError:
                d = None
        panel = _panel_path(it)
        entry = {"name": f"{panel}/{it.name}" if panel else it.name,
                 "type": it.socket_type.replace("NodeSocket", "").lower(), "default": d}
        for k in ("min_value", "max_value"):
            v = getattr(it, k, None)
            if v is not None and abs(v) < 1e8:
                entry[k[:3]] = round(v, 5)
        out.append(entry)
    for name, kind, node in ramps.ramp_nodes(tree):
        out.append({"name": name, "type": "colorramp" if kind == "color" else "ramp",
                    "preset": node.get(ramps.PRESET_KEY, "")})
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
    from . import ramps
    items = [it for it in mod.node_group.interface.items_tree if it.item_type == "SOCKET" and it.in_out == "INPUT"]
    by_path = {}
    for it in items:
        panel = _panel_path(it)
        by_path[f"{panel}/{it.name}" if panel else it.name] = it
    by_name = {}
    for it in items:
        by_name.setdefault(it.name, []).append(it)
    ramp_by_name = {}
    for rname, kind, node in ramps.ramp_nodes(mod.node_group):
        ramp_by_name.setdefault(rname, []).append((kind, node))
    inputs = mod.properties.inputs
    done, failed = {}, []
    for key, value in values.items():
        if key in ramp_by_name or key.rsplit("/", 1)[-1] in ramp_by_name and key not in by_path:
            # a ramp: set its preset (shared by every user of the group)
            preset = str(value).strip().lower().replace(" ", "_")
            for kind, node in ramp_by_name.get(key) or ramp_by_name[key.rsplit("/", 1)[-1]]:
                table = ramps.COLOR_PRESETS if kind == "color" else ramps.FLOAT_PRESETS
                if preset not in table:
                    failed.append(f"{key} (unknown preset '{value}' — {', '.join(sorted(table))})")
                    break
                ramps.apply(node, kind, node.get(ramps.RAMP_KEY), preset)
            else:
                done[key] = preset
            continue
        it = by_path.get(key)
        if it is None:
            cands = by_name.get(key, [])
            if len(cands) > 1:
                failed.append(f"{key} (ambiguous — say which panel: "
                              f"{', '.join(p for p, i in by_path.items() if i.name == key)})")
                continue
            it = cands[0] if cands else None
        item = getattr(inputs, it.identifier, None) if it is not None else None
        if item is None:
            failed.append(f"{key} (no such input)")
            continue
        st = it.socket_type
        try:
            if st in ("NodeSocketObject", "NodeSocketCollection", "NodeSocketMaterial", "NodeSocketImage"):
                coll = {"NodeSocketObject": bpy.data.objects, "NodeSocketCollection": bpy.data.collections,
                        "NodeSocketMaterial": bpy.data.materials, "NodeSocketImage": bpy.data.images}[st]
                ref = None if value in (None, "") else coll.get(str(value))
                if value not in (None, "") and ref is None:
                    failed.append(f"{key} (no {st[10:].lower()} named '{value}')")
                    continue
                item.value = ref
                done[key] = ref.name if ref else None
            else:
                item.value = value
                done[key] = value
        except (TypeError, ValueError, AttributeError) as e:
            failed.append(f"{key} ({e})")
    ob.update_tag()
    _undo_push("Formula: set parameters")
    _redraw()
    out = {"ok": not failed, "object": ob.name, "modifier": mod.name, "set": done}
    if failed:
        out["error"] = ("couldn't set: " + "; ".join(failed) + " — the others were set. Inputs: "
                        + ", ".join(by_path) + (" · ramps: " + ", ".join(ramp_by_name) if ramp_by_name else ""))
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


def _stats(a):
    from . import viewer
    st = viewer._attr_stats(a, len(a.data))
    if not st or "min" not in st:
        return None
    r = lambda v: [round(x, 5) for x in v] if st["width"] > 1 else round(v[0], 5)  # noqa: E731
    out = {"min": r(st["min"]), "mean": r(st["mean"]), "max": r(st["max"])}
    if st.get("bad"):
        out["nan_or_inf"] = st["bad"]
    return out


def _components(geo):
    for name in ("mesh", "pointcloud", "curves"):
        comp = getattr(geo, name, None)
        if comp is not None:
            yield name, comp
    inst = getattr(geo, "instances_pointcloud", None)
    if callable(inst):
        try:
            pc = inst()
        except Exception:
            pc = None
        if pc is not None:
            yield "instances", pc


def _volume(geo):
    vol = getattr(geo, "volume", None)
    if vol is None:
        return None
    grids = []
    try:
        vol.grids.load()
    except Exception:
        pass
    for g in getattr(vol, "grids", []):
        grids.append({"name": g.name, "type": getattr(g, "data_type", ""),
                      "channels": getattr(g, "channels", None)})
    return {"grids": grids}


def _inspect_once(ob, attributes, limit, elements):
    dg = bpy.context.evaluated_depsgraph_get()
    gs = ob.evaluated_get(dg).evaluated_geometry()
    comps = {}
    for comp_name, comp in _components(gs):
        attrs = {}
        for a in comp.attributes:
            if a.name.startswith("."):
                continue
            if attributes and a.name not in attributes:
                continue
            data = a.data
            n = len(data)
            idx = [i for i in elements if 0 <= i < n] if elements else range(min(limit, n))
            entry = {"domain": a.domain, "type": a.data_type, "count": n}
            if elements:
                entry["values"] = {str(i): _value(data[i]) for i in idx}
            else:
                entry["values"] = [_value(data[i]) for i in idx]
            st = _stats(a) if a.data_type != "STRING" else None
            if st:
                entry["stats"] = st
            attrs[a.name] = entry
        comps[comp_name] = attrs
    vol = _volume(gs)
    if vol:
        comps["volume"] = vol
    # detail values live on the geometry as a bundle-like set of single values; read what we can
    del gs
    return comps


def inspect(object_name, attributes=None, limit=8, frames=None, elements=None):
    """Evaluated attribute values after all modifiers — first values, min/mean/max, instances and volume
    grids. frames=[1, 24, 48] reads each frame; elements=[0, 10] reads those indices."""
    ob = _object(object_name)
    if ob is None:
        return _err(f"object '{object_name}' not found")
    limit = max(1, min(int(limit), 200))
    elements = [int(i) for i in (elements or [])][:50]
    if not hasattr(ob, "evaluated_geometry"):
        return _err("this Blender version can't read evaluated geometry")
    scene = bpy.context.scene
    out = {"ok": True, "object": ob.name}
    if frames:
        keep = scene.frame_current
        per = {}
        try:
            for f in [int(x) for x in frames][:12]:
                scene.frame_set(f)
                per[str(f)] = _inspect_once(ob, attributes, limit, elements)
        finally:
            scene.frame_set(keep)
        out["frames"] = per
    else:
        out["frame"] = scene.frame_current
        out["components"] = _inspect_once(ob, attributes, limit, elements)
    warnings = []
    for m in ob.modifiers:
        for w in getattr(m, "node_warnings", []):
            warnings.append(f"{m.name}: {getattr(w, 'message', str(w))}")
        if m.type == "NODES" and m.node_group is not None:
            for it in m.node_group.interface.items_tree:
                if it.item_type == "SOCKET" and it.in_out == "INPUT" and it.socket_type == "NodeSocketObject":
                    v = getattr(m.properties.inputs, it.identifier, None)
                    if v is not None and getattr(v, "value", 1) is None:
                        warnings.append(f"{m.name}: object input '{it.name}' is empty — set it with "
                                        f"formula_set_parameters")
    out["warnings"] = warnings
    return out


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


def language_guide():
    """The language reference written for the Blender that's actually running."""
    from . import ai
    return {"ok": True, "guide": ai.build_system_prompt(for_tools=True, target=caps.from_blender(bpy))}


def capabilities():
    t = caps.from_blender(bpy)
    return {"ok": True, "blender": caps.version_str(t.version),
            "features": [{"feature": k, "available": ok, "detail": text} for k, ok, text in caps.report(t)]}
