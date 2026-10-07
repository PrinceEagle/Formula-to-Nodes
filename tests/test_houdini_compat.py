# SPDX-License-Identifier: GPL-3.0-or-later
"""Houdini habits that should just work: globals, groups, vector2, while,
sample_*(), getbbox() with out-parameters, lookat() as a matrix."""

import math
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from formula_to_nodes import compiler  # noqa: E402
from formula_to_nodes.lang import FormulaError  # noqa: E402

try:
    import bpy  # noqa: F401
    HAVE_BPY = True
except ImportError:      # pragma: no cover
    HAVE_BPY = False

SNIPPETS = {
    "timeinc in sim": 'simulate {\n    f@age += @TimeInc;\n}',
    "timeinc outside": 'f@dt = @TimeInc;',
    "curveu": 'f@w = chramp("profile", @curveu);',
    "simtime": 'f@t = @SimTime + @SimFrame;',
    "group attr": 'i@group_top = @P.z > 0;',
    "inpointgroup": 'if (inpointgroup(0, "top", @ptnum)) @Cd = {1, 0, 0};',
    "setpointgroup": 'setpointgroup(0, "top", @ptnum, @P.z > 0);',
    "vector2": 'vector2 uv = set(@P.x, @P.y);\nf@u = uv.x;',
    "while": 'float d = 1;\nwhile (d > 0.1) { d *= 0.5; }\nf@d = d;',
    "inline while": 'float d = 1;\nwhile (d > 0.1) d *= 0.5;\nf@d = d;',
    "sample": 'v@a = sample_direction_uniform(set(rand(@ptnum), rand(@ptnum + 1), 0));\n'
              'v@b = sample_sphere_uniform(vector(rand(@ptnum)));\nv@c = sample_disk_uniform(vector(rand(@ptnum)));',
    "getbbox": 'vector mn, mx;\ngetbbox(0, mn, mx);\nf@h = mx.z - mn.z;',
    "lookat matrix": 'matrix3 m = lookat({0, 0, 0}, @P);\n4@xf = m;',
}


class TestCompiles(unittest.TestCase):
    def test_snippets(self):
        for name, src in SNIPPETS.items():
            with self.subTest(name):
                res = compiler.compile_source(src)
                self.assertFalse(any("TimeInc" in n or "curveu" in n for n in res.notes), res.notes)

    def test_timeinc_is_read_only(self):
        with self.assertRaises(FormulaError):
            compiler.compile_source("@TimeInc = 1;")

    def test_do_while_still_rejected(self):
        with self.assertRaises(FormulaError):
            compiler.compile_source("float d = 1;\ndo { d *= 0.5; } while (d > 0.1);")


@unittest.skipUnless(HAVE_BPY, "needs Blender's bpy module")
class TestBehaviour(unittest.TestCase):
    def setUp(self):
        import harness as H
        self.H = H
        H.clear()

    def test_while_runs_until_false(self):
        r = self.H.run('float d = 1;\nwhile (d > 0.1) { d *= 0.5; }\nf@d = d;')
        self.assertTrue(all(abs(v - 0.0625) < 1e-6 for v in r.attr("d")))

    def test_while_respects_maxiter(self):
        r = self.H.run('#pragma maxiter 3\nfloat d = 1;\nwhile (d > 0.01) { d *= 0.5; }\nf@d = d;')
        self.assertTrue(all(abs(v - 0.125) < 1e-6 for v in r.attr("d")))

    def test_groups_are_boolean_attributes(self):
        r = self.H.run('setpointgroup(0, "top", @ptnum, @P.z > 0);\n'
                       'f@inside = inpointgroup(0, "top", @ptnum);')
        tops = r.attr("top")
        self.assertEqual([float(t) for t in tops], r.attr("inside"))
        self.assertTrue(any(tops) and not all(tops))

    def test_sample_direction_is_unit(self):
        r = self.H.run('v@d = sample_direction_uniform(set(rand(@ptnum), rand(@ptnum + 1), 0));')
        self.assertTrue(all(abs(math.sqrt(sum(c * c for c in v)) - 1) < 1e-4 for v in r.attr("d")))

    def test_timeinc_in_simulation(self):
        def setup(ob, mod, tree):
            bpy.context.scene.frame_set(1)
            bpy.context.scene.frame_set(2)
        r = self.H.run('simulate {\n    f@age += @TimeInc;\n}', setup=setup)
        fps = bpy.context.scene.render.fps / bpy.context.scene.render.fps_base
        self.assertTrue(all(abs(a - 1 / fps) < 1e-4 for a in r.attr("age")), r.attr("age")[:3])

    def test_getbbox_out_params(self):
        r = self.H.run('vector mn, mx;\ngetbbox(0, mn, mx);\nf@h = mx.z - mn.z;')
        self.assertAlmostEqual(r.attr("h")[0], 2.0, places=4)


if __name__ == "__main__":
    unittest.main()
