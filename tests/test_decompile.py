# SPDX-License-Identifier: GPL-3.0-or-later
"""Nodes → script round trip: build a script, convert the node tree back
into a script, rebuild it into the same group and check that every evaluated
attribute is unchanged."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from formula_to_nodes import decompile, examples, recipes  # noqa: E402

try:
    import bpy  # noqa: F401
    HAVE_BPY = True
except ImportError:      # pragma: no cover
    HAVE_BPY = False

EXTRA = [
    ("math", 'f@a = log(@P.x + 2) + log(@P.y + 3, 10) + pow(@P.z, 2) + fmod(@ptnum, 3) + sqrt(abs(@P.x));'),
    ("align", 'p@orient = alignaxis(v@N, "x"); p@o2 = alignaxis(v@N, {0,0,1}); p@o3 = lookat({0,0,0}, v@P);'),
    ("ifelse", 'if (@P.z > 0) { @P.z *= 2; f@up = 1; } else { f@up = -1; }'),
    ("prim", 'runover(prim) { @Cd = set(rand(@primnum), 0.5, 1); }'),
    ("loop", 'float s = 0; for (int i = 0; i < 20; i++) { s += i * 0.1; } f@s = s + @P.x;'),
    ("sim", 'simulate { @P.z += deltatime * 0.5; }'),
    ("arrays", 'float vals[] = {1.5, 2, 3}; f@a = vals[@ptnum % 3] + sum(vals); v@b = vector(rand(@ptnum));'),
    ("detail", 'runover(detail) { f@area = sumof(@area, "prim"); }\nf@frac = @area / detail(0, "f@area");'),
]


def _cases():
    out = [(f"example:{e[0]}", e[3]) for e in examples.EXAMPLES]
    # the research-paper solvers are left out: each one is a long simulation, too slow to evaluate twice here
    out += [(f"recipe:{r.key}", r.script) for r in recipes.RECIPES
            if not r.test.get("inputs") and not r.test.get("prelude") and r.category != "Research Papers"]
    return out + [(f"extra:{k}", s) for k, s in EXTRA]


class TestDecompilePure(unittest.TestCase):
    def test_printf_specs(self):
        self.assertEqual(decompile._printf_spec(".3f", "FLOAT"), "%.3f")
        self.assertEqual(decompile._printf_spec("03", "INT"), "%03d")
        self.assertEqual(decompile._printf_spec("", "STRING"), "%s")
        self.assertEqual(decompile._printf_spec(".6f", "FLOAT"), "%f")


@unittest.skipUnless(HAVE_BPY, "needs Blender's bpy module")
class TestRoundTrip(unittest.TestCase):
    def snapshot(self, H, geo):
        out = {}
        for comp in ("mesh", "pointcloud", "curves"):
            c = getattr(geo, comp)
            if c is None:
                continue
            for a in c.attributes:
                if a.name.startswith(".") or a.name == "sharp_face":
                    continue
                r = H.Result(None, None, None, None, geo, [])
                out[(comp, a.name)] = r.attr(a.name, comp)
        return out

    def test_round_trip(self):
        import harness as H
        from formula_to_nodes import build, compiler
        H.ensure_registered()
        for name, src in _cases():
            with self.subTest(name):
                H.clear()
                r1 = H.run(src, subdivisions=2)
                before = self.snapshot(H, r1.geo)
                script, notes = decompile.decompile(decompile.extract(r1.tree))
                res = compiler.compile_source(script, target=H.target())
                build.build_group(res, script, "SCRIPT", target=r1.tree)
                dg = bpy.context.evaluated_depsgraph_get()
                dg.update()
                after = self.snapshot(H, r1.ob.evaluated_get(dg).evaluated_geometry())
                errors = [w.message for w in r1.mod.node_warnings if w.type == "ERROR"]
                self.assertEqual(errors, [], script)
                self.assertEqual(set(before), set(after), script)
                for key, vals in before.items():
                    other = after[key]
                    self.assertEqual(len(vals), len(other), f"{key}\n{script}")
                    bad = sum(1 for x, y in zip(vals, other)
                              if (x != y if isinstance(x, str) else not H.close(x, y, 1e-3)))
                    self.assertEqual(bad, 0, f"{key} differs\n{script}")


if __name__ == "__main__":
    unittest.main()
