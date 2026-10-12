# SPDX-License-Identifier: GPL-3.0-or-later
"""Dumps the node group a recipe compiles to (types, positions, links, frames) as json, in a
background Blender 5.3 — the real tree, laid out by the add-on — for drawing on promo cards.

    blender -b --factory-startup -P promo/node_map.py -- pfm_smoke weta_bubbles"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import bpy  # noqa: E402

from formula_to_nodes import build, caps, compiler, recipes  # noqa: E402


def dump(key):
    src = recipes.get(key).script
    res = compiler.compile_source(src, target=caps.from_blender(bpy))
    tree = build.build_group(res, src, "SCRIPT", name=key)
    nodes = []
    index = {}
    for n in tree.nodes:
        loc = n.location.copy()
        p = n.parent
        while p is not None:  # frame children are stored relative to their frame
            loc += p.location
            p = p.parent
        index[n.name] = len(nodes)
        ins = sum(1 for s in n.inputs if s.enabled and not s.hide)
        outs = sum(1 for s in n.outputs if s.enabled and not s.hide)
        nodes.append({"type": n.bl_idname, "label": n.label or n.bl_label, "x": loc.x, "y": loc.y,
                      "w": n.width, "rows": max(ins, outs), "frame": n.bl_idname == "NodeFrame",
                      "parent": n.parent.name if n.parent else None})
    links = [[index[l.from_node.name], index[l.to_node.name]] for l in tree.links]
    zones = sum(1 for n in tree.nodes if n.bl_idname.endswith("Input") and "Zone" not in n.bl_idname
                and n.bl_idname in ("GeometryNodeSimulationInput", "GeometryNodeRepeatInput"))
    types = {}
    for n in tree.nodes:
        if n.bl_idname not in ("NodeFrame", "NodeReroute"):
            types[n.bl_idname] = types.get(n.bl_idname, 0) + 1
    real = sum(types.values())
    return {"key": key, "nodes": nodes, "links": links, "count": real, "zones": zones, "types": types}


if __name__ == "__main__":
    keys = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else ["pfm_smoke", "weta_bubbles"]
    for k in [k for k in keys if k != "--"]:
        d = dump(k)
        path = os.path.join(HERE, "out", f"nodemap_{k}.json")
        json.dump(d, open(path, "w"))
        print("RESULT", json.dumps({"key": k, "file": path, "count": d["count"], "links": len(d["links"]),
                                    "top": sorted(d["types"].items(), key=lambda kv: -kv[1])[:12]}))
