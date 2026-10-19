# SPDX-License-Identifier: GPL-3.0-or-later
"""UI-side logic in a background Blender: live viewer compile + values, presets, rebuild safety."""

import os
import tempfile
import unittest

import bpy

import formula_to_nodes
from formula_to_nodes import build, compiler, presets, ui, viewer


def _plane():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    me = bpy.data.meshes.new("p")
    me.from_pydata([(x, y, 0) for x in range(4) for y in range(4)], [], [])
    ob = bpy.data.objects.new("P", me)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    return ob


class UITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            formula_to_nodes.register()
        except ValueError:      # already registered by another test module
            pass
        cls._tmp = tempfile.mkdtemp()
        presets._path = lambda: os.path.join(cls._tmp, "presets.json")

    def setUp(self):
        _plane()
        self.s = bpy.context.scene.formula_settings
        viewer.invalidate()

    def test_viewer_compiles_script_and_reports_errors(self):
        ui.set_script_text(self.s, 'f@h = @P.z * chf("amp", 2);')
        viewer._compile(self.s)
        res = viewer._state["result"]
        self.assertIsNotNone(res)
        self.assertIn("h", res.attrs)
        self.assertTrue(any(i.name == "amp" and i.default == 2 for i in res.iface))
        ui.set_script_text(self.s, "f@h = 1;\nf@g = nope(2);")
        viewer._compile(self.s)
        self.assertIsNone(viewer._state["result"])
        msg, line = viewer._state["error"]
        self.assertEqual(line, 2)
        # unchanged text doesn't recompile
        self.assertFalse(viewer._compile(self.s))

    def test_viewer_values_from_evaluated_object(self):
        ob = bpy.context.view_layer.objects.active
        src = 'f@h = @P.x * 2;'
        res = compiler.compile_source(src, **ui.compile_options())
        tree = build.build_group(res, src, "SCRIPT")
        mod = ob.modifiers.new("f", "NODES")
        mod.node_group = tree
        ui.set_script_text(self.s, src)
        viewer._compile(self.s)
        st = viewer._read_stats(bpy.context.window_manager.windows[0]) if bpy.context.window_manager.windows \
            else None
        if st is None:   # background: no window — read through the same code path with a stand-in
            class W:
                view_layer = bpy.context.view_layer
                scene = bpy.context.scene
            st = viewer._read_stats(W())
        attr = next(a for a in st["attrs"] if a["name"] == "h")
        self.assertAlmostEqual(attr["min"][0], 0.0)
        self.assertAlmostEqual(attr["max"][0], 6.0)
        self.assertAlmostEqual(attr["mean"][0], 3.0)
        self.assertEqual(attr["count"], 16)

    def test_default_formatting(self):
        res = compiler.compile_source('v@a = chv("dir", {0, 0, 1}); i@n = chi("count", 3); '
                                      'b@on = chb("flag", 1);', **ui.compile_options())
        got = {i.name: viewer.fmt_default(i) for i in res.iface if i.in_out == "INPUT"}
        self.assertEqual(got["dir"], "(0, 0, 1)")
        self.assertEqual(got["count"], "3")

    def test_presets_roundtrip(self):
        presets.save("My bumps", "f@a = 1;")
        presets.save("Another", "f@b = 2;")
        self.assertEqual([d["name"] for d in presets.load()], ["Another", "My bumps"])
        presets.save("My bumps", "f@a = 3;")
        self.assertEqual(presets.get("My bumps")["script"], "f@a = 3;")
        presets.delete("Another")
        self.assertIsNone(presets.get("Another"))
        with open(presets._path(), "w") as f:
            f.write("{broken")
        self.assertEqual(presets.load(), [])
        self.assertTrue(any(n.startswith("presets.json.broken") for n in os.listdir(self._tmp)))

    def test_fingerprint_and_broken_links(self):
        src = 'f@h = chf("a", 1);'
        res = compiler.compile_source(src, **ui.compile_options())
        tree = build.build_group(res, src, "SCRIPT")
        self.assertFalse(build.hand_edited(tree))
        tree.nodes[0].location.x += 300           # moving isn't an edit
        self.assertFalse(build.hand_edited(tree))
        tree.nodes.new("ShaderNodeMath")          # adding a node is
        self.assertTrue(build.hand_edited(tree))

        # a group using it, with a link into input "a"
        host = bpy.data.node_groups.new("host", "GeometryNodeTree")
        gn = host.nodes.new("GeometryNodeGroup")
        gn.node_tree = tree
        val = host.nodes.new("ShaderNodeValue")
        sock = next(s for s in gn.inputs if s.name == "a")
        host.links.new(val.outputs[0], sock)
        res2 = compiler.compile_source('f@h = chf("b", 1);', **ui.compile_options())
        self.assertEqual(build.broken_links(tree, res2), [("a", 1)])
        res3 = compiler.compile_source('f@h = chf("a", 2) + 1;', **ui.compile_options())
        self.assertEqual(build.broken_links(tree, res3), [])


if __name__ == "__main__":
    unittest.main()
