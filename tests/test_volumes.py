# SPDX-License-Identifier: GPL-3.0-or-later
"""Volumes: runover(voxel) { }, grid operations and the smoke solver.

Pure-Python checks always run; evaluation needs Blender's bpy module
(5.2 checks the Jacobi fallback, 5.3 the native Poisson and Rasterize nodes)."""

import math
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from formula_to_nodes import caps, compiler  # noqa: E402
from formula_to_nodes.lang import FormulaError  # noqa: E402

try:
    import bpy  # noqa: F401
    HAVE_BPY = True
except ImportError:      # pragma: no cover
    HAVE_BPY = False

T52, T53 = caps.Target((5, 2, 0)), caps.Target((5, 3, 0))

SMOKE = """
volumebounds({-0.5, -0.5, 0}, {0.5, 0.5, 1.5}, 0.1);
simulate {
    runover(voxel) {
        float src = max(1 - length(@P - {0, 0, 0.25}) * 5, 0);
        f@density = max(f@density, src);
        v@vel += {0, 0, 1} * f@density * 4 * deltatime;
    }
    volumeproject("vel");
    volumeadvect("vel", "vel", deltatime);
    volumeadvect("density", "vel", deltatime);
}
"""


def idnames(res):
    return [n.idname for n in res.graph.nodes]


class TestVolumeCompile(unittest.TestCase):
    def test_voxel_block_writes_grids(self):
        r = compiler.compile_source("volumebounds({0,0,0}, {1,1,1}, 0.1);\nrunover(voxel) { f@density = @P.z; }",
                                    target=T53)
        ids = idnames(r)
        self.assertIn("GeometryNodeCubeGridTopology", ids)
        self.assertIn("GeometryNodeFieldToGrid", ids)
        self.assertIn("GeometryNodeStoreNamedGrid", ids)

    def test_static_bounds_make_cube_voxels(self):
        r = compiler.compile_source("volumebounds({0,0,0}, {1,2,0.95}, 0.1);\nrunover(voxel) { f@d = 1; }",
                                    target=T53)
        topo = next(n for n in r.graph.nodes if n.idname == "GeometryNodeCubeGridTopology")
        self.assertEqual((topo.inputs["Resolution X"], topo.inputs["Resolution Y"], topo.inputs["Resolution Z"]),
                         (10, 20, 10))
        self.assertAlmostEqual(topo.inputs["Bounds Max"][2], 1.0)   # rounded up to whole voxels

    def test_project_uses_poisson_on_53_and_jacobi_on_52(self):
        r53 = compiler.compile_source(SMOKE, target=T53)
        self.assertIn("GeometryNodeGridSolvePoisson", idnames(r53))
        r52 = compiler.compile_source(SMOKE, target=T52)
        self.assertNotIn("GeometryNodeGridSolvePoisson", idnames(r52))
        self.assertIn("GeometryNodeGridLaplacian", idnames(r52))
        self.assertTrue(any("Jacobi" in n for n in r52.notes))

    def test_voxel_scope_errors(self):
        bad = {
            "float a = @P.x;\nrunover(voxel) { f@d = a; }": "per point",
            "runover(voxel) { f@d = @ptnum; }": "geometry elements",
            "runover(voxel) { scatter(10); }": "not voxels",
            "runover(voxel) { float a = 1; f@d = a; }\nf@x = @P.x;\nrunover(voxel) { s@t = \"x\"; }": "grids",
            "runover(voxel) { @P = 1; }": "voxels stay",
        }
        for src, word in bad.items():
            with self.subTest(src):
                with self.assertRaises(FormulaError) as cm:
                    compiler.compile_source("volumebounds({0,0,0}, {1,1,1}, 0.5);\n" + src, target=T53)
                self.assertIn(word, str(cm.exception))

    def test_grid_ops_need_whole_grids(self):
        with self.assertRaises(FormulaError):
            compiler.compile_source('if (@P.z > 0) volumeblur("density", 1);', target=T53)

    def test_rasterize_needs_53(self):
        with self.assertRaises(FormulaError) as cm:
            compiler.compile_source('rasterize("density", 1, 0.1);', target=T52)
        self.assertIn("5.3", str(cm.exception))
        compiler.compile_source('rasterize("density", @pscale, 0.1, "cubic");', target=T53)

    def test_vector_grid_names(self):
        r = compiler.compile_source('volumecurl("v@wind", "swirl");\nvolumeadvect("smoke", "wind", 0.1);',
                                    target=T53)
        self.assertIn("GeometryNodeGridCurl", idnames(r))
        with self.assertRaises(FormulaError):
            compiler.compile_source('volumeadvect("smoke", "f@wind", 0.1);', target=T53)


@unittest.skipUnless(HAVE_BPY, "needs Blender's bpy module")
class TestVolumeEvaluate(unittest.TestCase):
    def setUp(self):
        import harness as H
        self.H = H
        H.clear()

    def test_voxel_write_and_sample_back(self):
        r = self.H.run("volumebounds({-1.2,-1.2,-1.2}, {1.2,1.2,1.2}, 0.1);\n"
                       "runover(voxel) { f@zed = @P.z; v@pos = @P; }\n"
                       "f@s = volumesample(0, \"zed\", @P);\nv@sv = volumesamplev(0, \"pos\", @P);")
        zs = [p[2] for p in r.positions()]
        for z, s in zip(zs, r.attr("s")):
            self.assertAlmostEqual(z, s, delta=1e-3)
        for p, sv in zip(r.positions(), r.attr("sv")):
            self.assertTrue(self.H.close(p, sv, 1e-3))
        self.assertIsNotNone(r.geo.volume)

    def test_reads_ifs_and_indices(self):
        r = self.H.run("volumebounds({-1.2,-1.2,-1.2}, {1.2,1.2,1.2}, 0.2);\n"
                       "runover(voxel) {\n  f@a = 2;\n  f@b = f@a * 3;\n  if (@P.z > 0) f@b = -1;\n"
                       "  i@idx = @ix + @iy + @iz;\n}\n"
                       "f@sb = volumesample(0, \"b\", @P * 0.9);")
        for p, sb in zip(r.positions(), r.attr("sb")):
            if abs(p[2] * 0.9) > 0.15:
                self.assertAlmostEqual(sb, -1 if p[2] > 0 else 6, delta=1e-3)

    def test_advect_moves_contents(self):
        r = self.H.run("volumebounds({-1,-1,-1}, {1,1,2}, 0.05);\n"
                       "runover(voxel) { f@d = exp(-dot(@P, @P) * 20); v@vel = {0, 0, 1}; }\n"
                       "volumeadvect(\"d\", \"vel\", 0.5, \"semi\");\n"
                       "f@moved = volumesample(0, \"d\", {0, 0, 0.5});\n"
                       "f@origin = volumesample(0, \"d\", {0, 0, 0});")
        self.assertAlmostEqual(r.attr("moved")[0], 1.0, delta=0.05)
        self.assertLess(r.attr("origin")[0], 0.05)

    def test_project_removes_divergence(self):
        src = ("volumebounds({-1,-1,-1}, {1,1,1}, 0.05);\n"
               "runover(voxel) { v@vel = {0, 0, 1} * exp(-dot(@P, @P) * 8); }\n"
               "volumedivergence(\"vel\", \"before\");\n"
               "volumeproject(\"vel\", %s);\n"
               "volumedivergence(\"vel\", \"after\");\n"
               "f@before = abs(volumesample(0, \"before\", @P * 0.3));\n"
               "f@after = abs(volumesample(0, \"after\", @P * 0.3));")
        iters = "100" if bpy.app.version >= (5, 3, 0) else "200"
        r = self.H.run(src % iters, kind="ico", subdivisions=2)
        before, after = sum(r.attr("before")), sum(r.attr("after"))
        self.assertGreater(before, 1.0)
        self.assertLess(after, before * (0.1 if bpy.app.version >= (5, 3, 0) else 0.6))

    def test_mesh_sdf_and_iso_surface(self):
        r = self.H.run('volumefrommesh("sdf", 0.05);\nf@inside = volumesample(0, "sdf", {0, 0, 0});\n'
                       'f@outside = volumesample(0, "sdf", {0, 0, 1.08});', kind="cube", size=2.0)
        self.assertLess(r.attr("inside")[0], 0)
        self.assertGreater(r.attr("outside")[0], 0)
        r = self.H.run('volumebounds({-1,-1,-1}, {1,1,1}, 0.05);\n'
                       'runover(voxel) { f@ball = 0.6 - length(@P); }\n'
                       'volumetomesh("ball", 0);')
        rad = [math.sqrt(sum(c * c for c in p)) for p in r.positions()]
        self.assertGreater(len(rad), 100)
        self.assertAlmostEqual(sum(rad) / len(rad), 0.6, delta=0.05)

    def test_keep_volume_drops_mesh(self):
        r = self.H.run('volumebounds({-1,-1,-1}, {1,1,1}, 0.25);\nrunover(voxel) { f@density = 1; }\n'
                       'keep("volume");')
        self.assertEqual(r.count("mesh"), 0)
        self.assertIsNotNone(r.geo.volume)

    @unittest.skipUnless(HAVE_BPY and bpy.app.version >= (5, 3, 0), "Rasterize Points is Blender 5.3+")
    def test_rasterize_points(self):
        r = self.H.run('topoints();\nrasterize("density", 1, 0.1, "linear");\n'
                       'f@d = volumesample(0, "density", @P);')
        self.assertTrue(all(d > 0.1 for d in r.attr("d", "pointcloud")))

    def test_smoke_simulation_rises(self):
        H = self.H
        ob = H.make_object("plane")
        scn = bpy.context.scene
        scn.frame_start = 1
        r = H.run(SMOKE + '\nf@probe = 0;', ob=ob, frame=1)
        top_density = []
        for f in range(2, 26):
            scn.frame_set(f)
        dg = bpy.context.evaluated_depsgraph_get()
        geo = ob.evaluated_get(dg).evaluated_geometry()
        self.assertIsNotNone(geo.volume)
        # sample the density column through a second, plain script on a line of points
        res = compiler.compile_source(
            'f@d = volumesample(chobj("smoke"), "density", @P);', target=H.target())
        from formula_to_nodes import build
        tree = build.build_group(res, "", "SCRIPT", name="probe")
        line = H.make_object("line", count=15)
        for i, v in enumerate(line.data.vertices):
            v.co = (0.0, 0.0, 0.05 + i * 0.1)
        mod = line.modifiers.new("p", "NODES")
        mod.node_group = tree
        H.set_input(mod, tree, "smoke", ob)
        dg.update()
        g = line.evaluated_get(dg).evaluated_geometry()
        d = [x.value for x in g.mesh.attributes["d"].data]
        self.assertFalse(any(x != x for x in d))       # no NaN
        risen = max((i for i, x in enumerate(d) if x > 0.05), default=0)
        self.assertGreaterEqual(risen, 6)                # smoke climbed above 0.65
        del r, top_density


if __name__ == "__main__":
    unittest.main()
