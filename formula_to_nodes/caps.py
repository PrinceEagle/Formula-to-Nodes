# SPDX-License-Identifier: GPL-3.0-or-later
"""Capability table: which language features need which Blender version and
which node types. Pure Python.

The compiler checks features against a *target* (a Blender version tuple and,
inside Blender, the set of node types that really exist), so a script that
uses something newer fails with a clear message instead of a broken tree.
The same table filters the function reference shown to people and to the AI.
"""

MIN_BLENDER = (5, 2, 0)
TESTED = ((5, 2, 2),)          # versions the test suite evaluated geometry on
DEFAULT_TARGET = (5, 2, 0)

# key: (minimum version, what it enables, node types that must exist)
FEATURES = {
    "lists": ((5, 2, 0), "arrays (float a[] = {...})",
              ("GeometryNodeFieldToList", "GeometryNodeListGetItem", "GeometryNodeListLength")),
    "geometry_bundles": ((5, 2, 0), "detail attributes (#runover detail, detail(), setdetailattrib())",
                         ("GeometryNodeSetGeometryBundle", "GeometryNodeGetGeometryBundle",
                          "NodeStoreBundleItem", "NodeGetBundleItem")),
    "mesh_bevel": ((5, 2, 0), "bevel()", ("GeometryNodeMeshBevel",)),
    "sound": ((5, 2, 0), "spectrum() audio sampling", ("GeometryNodeSampleSoundFrequencies",)),
    "nurbs": ((5, 2, 0), "@nurbs_weight and @nurbs_order", ("GeometryNodeSetNURBSOrder",
                                                            "GeometryNodeSetNURBSWeight")),
    "string_tools": ((5, 2, 0), "strip(), reverse() on text", ("FunctionNodeTrimString",
                                                                "FunctionNodeReverseString")),
    "field_stats": ((5, 2, 0), "grouped avgof()/minof()/maxof()/stdevof()",
                    ("GeometryNodeFieldAverage", "GeometryNodeFieldMinAndMax", "GeometryNodeFieldVariance")),
    "string_fields": ((5, 3, 0), "per-element text: s@name attributes and text that varies per element", ()),
    "empty_modifiers": ((5, 3, 0), "Geometry Nodes modifiers on empties (effectors)", ()),
}


def version_str(v):
    return ".".join(str(x) for x in tuple(v)[:3])


class Target:
    """What the compiled tree will run on."""

    def __init__(self, version=None, node_types=None):
        self.version = tuple(version or DEFAULT_TARGET)[:3]
        self.node_types = node_types      # None = assume every node of that version exists

    def supports(self, feature):
        need, _label, nodes = FEATURES[feature]
        if self.version < need:
            return False
        if self.node_types is not None and any(n not in self.node_types for n in nodes):
            return False
        return True

    def why_not(self, feature):
        need, label, nodes = FEATURES[feature]
        if self.version < need:
            return f"{label} needs Blender {version_str(need)} or newer (this is {version_str(self.version)})"
        missing = [n for n in nodes if self.node_types is not None and n not in self.node_types]
        if missing:
            return f"{label} isn't available in this Blender build (missing {', '.join(missing)})"
        return None

    def __repr__(self):
        return f"Target({version_str(self.version)})"


def from_blender(bpy):
    """Target for the running Blender: its version plus the node types it has."""
    names = {n for n in dir(bpy.types) if n.startswith(("GeometryNode", "FunctionNode", "ShaderNode", "Node"))}
    return Target(bpy.app.version, names)


def report(target):
    """[(feature, ok, text)] for the UI / MCP capability listing."""
    out = []
    for key, (need, label, _nodes) in FEATURES.items():
        ok = target.supports(key)
        out.append((key, ok, label if ok else target.why_not(key)))
    return out
