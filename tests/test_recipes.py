# SPDX-License-Identifier: GPL-3.0-or-later
"""Every recipe compiles (pure Python) and evaluates in Blender without
errors, with the inputs it asks for."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from formula_to_nodes import compiler, recipes  # noqa: E402

try:
    import bpy  # noqa: F401
    HAVE_BPY = True
except ImportError:      # pragma: no cover
    HAVE_BPY = False


class TestRecipesCompile(unittest.TestCase):
    def test_all_compile(self):
        for r in recipes.RECIPES:
            with self.subTest(r.key):
                src = r.script
                if r.test.get("prelude") and not r.test.get("split"):
                    src = r.test["prelude"] + "\n" + src
                compiler.compile_source(src)

    def test_unique_keys_and_categories(self):
        keys = [r.key for r in recipes.RECIPES]
        self.assertEqual(len(keys), len(set(keys)))
        for r in recipes.RECIPES:
            self.assertIn(r.category, recipes.CATEGORIES)
            self.assertTrue(r.description.endswith((".", ")")), r.key)

    def test_search(self):
        self.assertIn("scatter_instance", [r.key for r in recipes.search("scatter instance")])


def _input_value(kind, H):
    if kind == "cube":
        return H.make_object("cube", size=0.3)
    if kind == "collection":
        coll = bpy.data.collections.new("Rocks")
        bpy.context.scene.collection.children.link(coll)
        for i in range(2):
            ob = H.make_object("cube", size=0.2 + 0.1 * i)
            bpy.context.scene.collection.objects.unlink(ob)
            coll.objects.link(ob)
        return coll
    raise ValueError(kind)


@unittest.skipUnless(HAVE_BPY, "needs Blender's bpy module")
class TestRecipesEvaluate(unittest.TestCase):
    def test_all_evaluate(self):
        import harness as H
        H.ensure_registered()
        from formula_to_nodes import build
        for r in recipes.RECIPES:
            with self.subTest(r.key):
                H.clear()
                for c in list(bpy.data.collections):
                    bpy.data.collections.remove(c)
                spec = r.test
                kind = spec.get("kind", "ico")
                ob = H.make_object(kind, **({"subdivisions": 3} if kind == "ico" else
                                            {"x": 30, "y": 30} if kind == "grid" else {}))
                values = {name: _input_value(k, H) for name, k in spec.get("inputs", {}).items()}
                mods = []
                scripts = [r.script]
                if spec.get("prelude"):
                    scripts = [spec["prelude"], r.script] if spec.get("split") else [spec["prelude"] + "\n" + r.script]
                for i, src in enumerate(scripts):
                    res = compiler.compile_source(src, target=H.target())
                    tree = build.build_group(res, src, "SCRIPT", name=f"{r.key}_{i}")
                    mod = ob.modifiers.new(f"m{i}", "NODES")
                    mod.node_group = tree
                    for name, value in values.items():
                        if any(it.name == name for it in tree.interface.items_tree):
                            H.set_input(mod, tree, name, value)
                    mods.append(mod)
                scene = bpy.context.scene
                scene.frame_set(1)
                for f in (2, 3):
                    scene.frame_set(f)
                dg = bpy.context.evaluated_depsgraph_get()
                geo = ob.evaluated_get(dg).evaluated_geometry()
                errors = [w.message for m in mods for w in m.node_warnings if w.type == "ERROR"]
                self.assertEqual(errors, [], r.key)
                warns = [w.message for m in mods for w in m.node_warnings if w.type == "WARNING"]
                self.assertEqual(warns, [], r.key)
                size = 0
                for comp in ("mesh", "pointcloud", "curves"):
                    c = getattr(geo, comp)
                    if c is not None:
                        size += len(c.attributes["position"].data) if "position" in c.attributes else 0
                ip = geo.instances_pointcloud()
                if ip is not None:
                    size += len(ip.points)
                self.assertGreater(size, 0, r.key)
                scene.frame_set(1)


if __name__ == "__main__":
    unittest.main()
