# SPDX-License-Identifier: GPL-3.0-or-later
"""Builds scripts into real node trees in Blender and checks evaluated values.

Run with Blender's Python module installed (pip install bpy==5.2.*):
    python -m unittest discover -s tests -p "test_blender*.py"
"""

import math
import unittest

try:
    import bpy  # noqa: F401
    HAVE_BPY = True
except ImportError:      # pragma: no cover
    HAVE_BPY = False

if HAVE_BPY:
    import harness as H
    from formula_to_nodes import compiler, build, ramps
    from formula_to_nodes.lang import FormulaError


def _ico_points():
    H.clear()
    ob = H.make_object("ico")
    return [tuple(v.co) for v in ob.data.vertices], ob


def length(v):
    return math.sqrt(sum(x * x for x in v))


@unittest.skipUnless(HAVE_BPY, "needs Blender's bpy module")
class TestDomains(unittest.TestCase):
    def setUp(self):
        H.clear()

    def test_runover_prim_writes_face_attributes(self):
        r = H.run("#runover prim\nf@ar = @area * 2;\ni@pn = @primnum;")
        self.assertEqual(r.domain_of("ar"), "FACE")
        me = r.ob.data
        areas = [p.area for p in me.polygons]
        self.assertTrue(all(H.close(a, 2 * b, 1e-3) for a, b in zip(r.attr("ar"), areas)))
        self.assertEqual(r.attr("pn"), list(range(len(areas))))

    def test_runover_block_and_promotion(self):
        r = H.run("runover(face) { f@fz = @P.z; }\nf@pz = @P.z;\nfloat c = 0;\n"
                  "runover(face) { c = @P.z; }\nf@back = c;")
        self.assertEqual(r.domain_of("fz"), "FACE")
        self.assertEqual(r.domain_of("pz"), "POINT")
        # a face value read per point is averaged over the point's faces
        top = max(range(r.count()), key=lambda i: r.attr("pz")[i])
        self.assertLess(r.attr("back")[top], r.attr("pz")[top])

    def test_vertex_runover(self):
        r = H.run("#runover vertex\ni@vp = @ptnum;\ni@vx = @vtxnum;\ni@vf = @primnum;")
        me = r.ob.data
        self.assertEqual(r.domain_of("vp"), "CORNER")
        self.assertEqual(r.attr("vp"), [l.vertex_index for l in me.loops])
        self.assertEqual(r.attr("vx"), list(range(len(me.loops))))
        faces = [p.index for p in me.polygons for _ in p.loop_indices]
        self.assertEqual(r.attr("vf"), faces)

    def test_edge_runover(self):
        r = H.run("#runover edge\nf@one = 1;")
        self.assertEqual(r.domain_of("one"), "EDGE")
        self.assertEqual(len(r.attr("one")), 30)

    def test_point_attribute_errors_in_face_mode(self):
        with self.assertRaises(FormulaError) as cm:
            compiler.compile_source("#runover prim\n@P.z += 1;")
        self.assertIn("points", str(cm.exception))

    def test_runover_argument(self):
        r = H.run("f@x = @elemnum;", runover="prim")
        self.assertEqual(r.domain_of("x"), "FACE")


@unittest.skipUnless(HAVE_BPY, "needs Blender's bpy module")
class TestDetail(unittest.TestCase):
    def setUp(self):
        H.clear()

    def test_detail_mode_and_read_back(self):
        r = H.run("runover(detail) { f@total = npoints(0) * 2; }\nf@t = detail(0, \"total\");\nf@t2 = f@total;")
        self.assertTrue(all(H.close(x, 24) for x in r.attr("t")))
        self.assertTrue(all(H.close(x, 24) for x in r.attr("t2")))

    def test_setdetailattrib_add_and_max(self):
        pts, ob = _ico_points()
        r = H.run('setdetailattrib(0, "sz", @P.z, "add");\nsetdetailattrib(0, "mx", @P.x, "max");\n'
                  'f@s = detail(0, "sz");\nf@m = detail(0, "mx");', ob=ob)
        self.assertAlmostEqual(r.attr("s")[0], sum(p[2] for p in pts), places=4)
        self.assertAlmostEqual(r.attr("m")[0], max(p[0] for p in pts), places=4)

    def test_detail_conditional_count(self):
        r = H.run('if (@P.z > 0) setdetailattrib(0, "n", 1, "add");\nf@n = detail(0, "n");')
        self.assertTrue(H.close(r.attr("n")[0], 6))

    def test_per_element_value_into_detail_is_an_error(self):
        with self.assertRaises(FormulaError):
            compiler.compile_source("#runover detail\nf@x = @P.z;")


@unittest.skipUnless(HAVE_BPY, "needs Blender's bpy module")
class TestTypes(unittest.TestCase):
    def setUp(self):
        H.clear()

    def test_color_attribute(self):
        r = H.run("@Cd = {1, 0.5, 0};\nc@tint = {0, 0, 1};")
        a = r.comp("mesh").attributes["Cd"]
        self.assertEqual(a.data_type, "FLOAT_COLOR")
        self.assertTrue(H.close(r.attr("Cd")[0], (1, 0.5, 0, 1)))
        self.assertEqual(r.comp("mesh").attributes["tint"].data_type, "FLOAT_COLOR")

    def test_quaternion_rotate(self):
        r = H.run("p@orient = quaternion(radians(90), {0, 0, 1});\nv@r = qrotate(p@orient, {1, 0, 0});\n"
                  "v@r2 = p@orient * {1, 0, 0};\nvector4 q = {0, 0, 0, 1};\nv@r3 = qrotate(q, {0, 1, 0});")
        self.assertEqual(r.comp("mesh").attributes["orient"].data_type, "QUATERNION")
        self.assertTrue(H.close(r.attr("r")[0], (0, 1, 0), 1e-4))
        self.assertTrue(H.close(r.attr("r2")[0], (0, 1, 0), 1e-4))
        self.assertTrue(H.close(r.attr("r3")[0], (0, 1, 0), 1e-4))

    def test_qmultiply_order(self):
        # rotate 90° about Z, then 90° about X: X → Y → Z
        r = H.run("vector4 a = quaternion(radians(90), {0, 0, 1});\nvector4 b = quaternion(radians(90), {1, 0, 0});\n"
                  "v@q1 = qrotate(qmultiply(b, a), {1, 0, 0});\nv@w = qrotate(b * a, {1, 0, 0});")
        self.assertTrue(H.close(r.attr("q1")[0], (0, 0, 1), 1e-4))
        self.assertTrue(H.close(r.attr("w")[0], (0, 0, 1), 1e-4))

    def test_dihedral_and_lookat(self):
        pts, ob = _ico_points()
        r = H.run("v@d = qrotate(dihedral({0, 0, 1}, normalize(@P)), {0, 0, 1});\n"
                  "v@fw = qrotate(lookat(@P, {0, 0, 0}), {0, 0, -1});", ob=ob)
        for p, d, fw in zip(pts, r.attr("d"), r.attr("fw")):
            n = tuple(x / length(p) for x in p)
            self.assertTrue(H.close(d, n, 1e-3), (p, d))
            self.assertTrue(H.close(fw, tuple(-x for x in n), 1e-3), (p, fw))

    def test_matrix(self):
        pts, ob = _ico_points()
        r = H.run("matrix m = maketransform({1, 2, 3}, {0, 0, 0}, {2, 2, 2});\nv@t = v@P * m;\n"
                  "v@back = v@t * invert(m);\nv@tr = gettranslation(m);", ob=ob)
        for p, t, b in zip(pts, r.attr("t"), r.attr("back")):
            self.assertTrue(H.close(t, (2 * p[0] + 1, 2 * p[1] + 2, 2 * p[2] + 3), 1e-4))
            self.assertTrue(H.close(b, p, 1e-4))
        self.assertTrue(H.close(r.attr("tr")[0], (1, 2, 3)))

    def test_strings_single(self):
        r = H.run('string s = sprintf("pt_%03d", 7);\ni@l = strlen(s);\ni@f = find(s, "07");\n'
                  'i@u = toupper(s) == "PT_007";\ni@sl = strlen(s[1:3]);\nprintf("hello %g and %.2f", 1.5, pi);')
        self.assertEqual(r.attr("l")[0], 6)
        self.assertEqual(r.attr("f")[0], 4)
        self.assertEqual(r.attr("u")[0], 1)
        self.assertEqual(r.attr("sl")[0], 2)
        self.assertIn("INFO:hello 1.5 and 3.14", r.warnings)

    def test_string_attributes_need_5_3(self):
        with self.assertRaises(FormulaError) as cm:
            compiler.compile_source('s@name = "x";', target=(5, 2, 0))
        self.assertIn("5.3", str(cm.exception))
        compiler.compile_source('s@name = "x";', target=(5, 3, 0))


@unittest.skipUnless(HAVE_BPY, "needs Blender's bpy module")
class TestFunctionsAndLoops(unittest.TestCase):
    def setUp(self):
        H.clear()

    def test_user_function(self):
        pts, ob = _ico_points()
        r = H.run("float sq(float x) { return x * x; }\nf@a = sq(@P.z);", ob=ob)
        self.assertTrue(all(H.close(a, p[2] ** 2) for a, p in zip(r.attr("a"), pts)))

    def test_early_return_and_by_reference(self):
        pts, ob = _ico_points()
        r = H.run("float f(float x) { if (x < 0) return -1; return x * 2; }\n"
                  "void bump(float v) { v += 10; }\nfloat k = 1;\nbump(k);\nf@a = f(@P.z);\nf@k = k;", ob=ob)
        for a, p in zip(r.attr("a"), pts):
            self.assertTrue(H.close(a, -1 if p[2] < 0 else 2 * p[2]))
        self.assertTrue(H.close(r.attr("k")[0], 11))

    def test_void_function_writes_attributes(self):
        pts, ob = _ico_points()
        r = H.run("void lift(float h) { @P.z += h; }\nvector rest = @P;\nlift(0.5);\nf@d = distance(rest, @P);",
                  ob=ob)
        self.assertTrue(all(H.close(d, 0.5) for d in r.attr("d")))

    def test_for_unrolled_and_zone(self):
        r = H.run("float s = 0;\nfor (int i = 0; i < 4; i++) { s += i; }\nf@s = s;")
        self.assertTrue(H.close(r.attr("s")[0], 6))
        H.clear()
        r = H.run('float s = 0;\nint n = chi("n", 20);\nfor (int i = 0; i < n; i++) { s += i; }\nf@s = s;',
                  params={"n": 7})
        self.assertTrue(H.close(r.attr("s")[0], 21))

    def test_for_loop_with_geometry_writes(self):
        pts, ob = _ico_points()
        r = H.run("for (int i = 0; i < 12; i++) { @P.z += 0.1; }\nf@z = @P.z;", ob=ob)
        self.assertTrue(all(H.close(z, p[2] + 1.2, 1e-4) for z, p in zip(r.attr("z"), pts)))

    def test_repeat_carries_variables(self):
        pts, ob = _ico_points()
        r = H.run("float acc = 0;\nrepeat(5) { acc += 2; @P.z += 0.1; }\nf@acc = acc;", ob=ob)
        self.assertTrue(H.close(r.attr("acc")[0], 10))
        self.assertTrue(H.close(r.positions()[0][2], pts[0][2] + 0.5, 1e-4))

    def test_per_point_values_carried(self):
        pts, ob = _ico_points()
        r = H.run("float h = @P.z;\nrepeat(3) { h = h * 2; }\nf@h = h;", ob=ob)
        self.assertTrue(all(H.close(h, p[2] * 8, 1e-4) for h, p in zip(r.attr("h"), pts)))

    def test_include_and_define(self):
        pts, ob = _ico_points()
        r = H.run('#include "falloff.h"\n#define R 1.5\nf@w = falloff_linear(length(@P), R);', ob=ob)
        self.assertTrue(all(H.close(w, 1 - length(p) / 1.5, 1e-4) for w, p in zip(r.attr("w"), pts)))


@unittest.skipUnless(HAVE_BPY, "needs Blender's bpy module")
class TestArrays(unittest.TestCase):
    def setUp(self):
        H.clear()

    def test_palette(self):
        r = H.run("vector pal[] = {{1, 0, 0}, {0, 1, 0}, {0, 0, 1}};\n@Cd = pal[@ptnum % len(pal)];")
        cols = r.attr("Cd")
        self.assertTrue(H.close(cols[0][:3], (1, 0, 0)))
        self.assertTrue(H.close(cols[1][:3], (0, 1, 0)))
        self.assertTrue(H.close(cols[5][:3], (0, 0, 1)))

    def test_reductions_and_edits(self):
        r = H.run("float w[] = array(4, 1, 3, 2);\nf@s = sum(w);\nf@mx = max(w);\nf@mn = min(w);\n"
                  "append(w, 10);\nf@n = len(w);\nfloat srt[] = sort(w);\nf@first = srt[0];\nf@last = w[-1];\n"
                  "i@at = find(w, 3);\nfloat t = 0;\nforeach (float x; w) { t += x; }\nf@t = t;")
        self.assertTrue(H.close(r.attr("s")[0], 10))
        self.assertTrue(H.close(r.attr("mx")[0], 4))
        self.assertTrue(H.close(r.attr("mn")[0], 1))
        self.assertTrue(H.close(r.attr("n")[0], 5))
        self.assertTrue(H.close(r.attr("first")[0], 1))
        self.assertTrue(H.close(r.attr("last")[0], 10))
        self.assertEqual(r.attr("at")[0], 2)
        self.assertTrue(H.close(r.attr("t")[0], 20))

    def test_per_point_array_items_are_rejected(self):
        with self.assertRaises(FormulaError):
            compiler.compile_source("float a[] = {@P.z, 1};\nf@x = a[0];")


@unittest.skipUnless(HAVE_BPY, "needs Blender's bpy module")
class TestEffects(unittest.TestCase):
    def setUp(self):
        H.clear()

    def test_removepoint(self):
        pts, ob = _ico_points()
        r = H.run("if (@P.z > 0.5) removepoint(0, @ptnum);\nf@z = @P.z;", ob=ob)
        self.assertEqual(r.count(), sum(1 for p in pts if p[2] <= 0.5))

    def test_removepoint_fixed_index(self):
        r = H.run("removepoint(0, 0);")
        self.assertEqual(r.count(), 11)

    def test_removeprim(self):
        r = H.run("#runover prim\nif (@P.z > 0) removeprim(0, @primnum, 1);")
        self.assertEqual(r.count("mesh", "FACE"), 10)

    def test_addpoint_keeps_order(self):
        pts, ob = _ico_points()
        r = H.run("addpoint(0, @P * 2);", ob=ob)
        got = r.positions()
        self.assertEqual(len(got), 24)
        self.assertTrue(all(H.close(a, b) for a, b in zip(got[:12], pts)))
        self.assertTrue(all(H.close(a, tuple(2 * x for x in b)) for a, b in zip(got[12:], pts)))

    def test_warning_only_when_branch_runs(self):
        r = H.run('if (@P.z > 0.9) warning("top point found");\nf@x = 1;')
        self.assertIn("WARNING:top point found", r.warnings)
        H.clear()
        r = H.run('if (@P.z > 5) warning("never");\nf@x = 1;')
        self.assertEqual(r.warnings, [])


@unittest.skipUnless(HAVE_BPY, "needs Blender's bpy module")
class TestSampling(unittest.TestCase):
    def setUp(self):
        H.clear()

    def test_opinput_and_point(self):
        cube = H.make_object("cube")
        cube_pts = [tuple(v.co) for v in cube.data.vertices]
        r = H.run('v@o = @opinput1_P;\nf@px = point(1, "P", 3).x;\ni@n1 = npoints(1);',
                  setup=lambda ob, mod, tree: H.set_input(mod, tree, "Input 1", cube))
        self.assertTrue(H.close(r.attr("o")[0], cube_pts[0]))
        self.assertTrue(H.close(r.attr("px")[0], cube_pts[3][0]))
        self.assertEqual(r.attr("n1")[0], 8)

    def test_xyzdist_minpos(self):
        pts, ob = _ico_points()
        r = H.run("f@d = xyzdist(0, @P * 2);\nv@m = minpos(0, @P * 2);", ob=ob)
        for d, m, p in zip(r.attr("d"), r.attr("m"), pts):
            self.assertTrue(H.close(d, length(tuple(2 * x - y for x, y in zip(p, m))), 1e-4))

    def test_intersect(self):
        r = H.run("vector hp;\nint pr = intersect(0, {0, 0, 5}, {0, 0, -10}, hp);\nf@pr = pr;\nv@hp = hp;\n"
                  "i@miss = intersect(0, {5, 5, 5}, {0, 0, 1});")
        self.assertGreaterEqual(r.attr("pr")[0], 0)
        self.assertTrue(H.close(r.attr("hp")[0], (0, 0, 1), 1e-4))
        self.assertEqual(r.attr("miss")[0], -1)

    def test_topology(self):
        r = H.run("i@nc = neighbourcount(0, @ptnum);\ni@n0 = neighbour(0, @ptnum, 0);\ni@bad = neighbour(0, @ptnum, 9);")
        self.assertTrue(all(c == 5 for c in r.attr("nc")))
        me = r.ob.data
        adj = {i: set() for i in range(len(me.vertices))}
        for e in me.edges:
            a, b = e.vertices
            adj[a].add(b)
            adj[b].add(a)
        for i, n0 in enumerate(r.attr("n0")):
            self.assertIn(n0, adj[i])
        self.assertTrue(all(b == -1 for b in r.attr("bad")))

    def test_primpoint(self):
        r = H.run("#runover prim\ni@p0 = primpoint(0, @primnum, 0);\ni@nv = primvertexcount(0, @primnum);")
        me = r.ob.data
        self.assertEqual(r.attr("p0"), [p.vertices[0] for p in me.polygons])
        self.assertTrue(all(n == 3 for n in r.attr("nv")))

    def test_aggregates(self):
        pts, ob = _ico_points()
        r = H.run("f@avg = avgof(@P.z);\nf@cnt = countof(@P.z > 0);\nf@mxz = maxof(@P.z);\nf@at = atindex(@P.z, 0);",
                  ob=ob)
        self.assertTrue(H.close(r.attr("avg")[0], sum(p[2] for p in pts) / 12, 1e-5))
        self.assertTrue(H.close(r.attr("cnt")[0], 6))
        self.assertTrue(H.close(r.attr("mxz")[0], max(p[2] for p in pts)))
        self.assertTrue(H.close(r.attr("at")[5], pts[0][2]))

    def test_volume_sample_from_input(self):
        H.clear()
        src = H.make_object("plane")
        gen = bpy.data.node_groups.new("vol", "GeometryNodeTree")
        gen.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        vc = gen.nodes.new("GeometryNodeVolumeCube")
        vc.inputs["Density"].default_value = 0.75
        out = gen.nodes.new("NodeGroupOutput")
        gen.links.new(vc.outputs[0], out.inputs[0])
        m = src.modifiers.new("vol", "NODES")
        m.node_group = gen
        r = H.run('f@v = volumesample(1, "density", @P * 0.5);',
                  setup=lambda ob, mod, tree: H.set_input(mod, tree, "Input 1", src))
        self.assertTrue(H.close(r.attr("v")[0], 0.75, 1e-3))


@unittest.skipUnless(HAVE_BPY, "needs Blender's bpy module")
class TestGeometryOps(unittest.TestCase):
    def setUp(self):
        H.clear()

    def test_scatter_sets_normals_and_orient(self):
        r = H.run("scatter(40, 1);\nf@h = @P.z;\nv@n2 = @N;")
        n = r.count("pointcloud")
        self.assertGreater(n, 10)
        for p, nn in zip(r.attr("position", "pointcloud"), r.attr("n2", "pointcloud")):
            self.assertAlmostEqual(length(nn), 1.0, places=3)
            self.assertGreater(sum(a * b for a, b in zip(p, nn)), 0)    # outward on a sphere

    def test_scatter_inside_if_uses_selection(self):
        r = H.run("if (@P.z > 0) scatter(200, 3);")
        zs = [p[2] for p in r.attr("position", "pointcloud")]
        self.assertGreater(min(zs), -0.5)             # faces whose centre is above z = 0
        self.assertGreater(sum(zs) / len(zs), 0.3)

    def test_instance_and_instance_mode(self):
        H.clear()
        leaf = H.make_object("cube", size=0.2)
        r = H.run('scatter(10, 3);\n@pscale = 0.5;\ninstance(chobj("Leaf"));\nv@s = @scale;\n@scale *= 2;\n'
                  'v@s2 = @scale;',
                  setup=lambda ob, mod, tree: H.set_input(mod, tree, "Leaf", leaf))
        self.assertGreater(r.count("instances"), 0)
        self.assertTrue(all(H.close(s, (0.5, 0.5, 0.5), 1e-4) for s in r.attr("s", "instances")))
        self.assertTrue(all(H.close(s, (1, 1, 1), 1e-4) for s in r.attr("s2", "instances")))

    def test_extrude_bevel_subdiv(self):
        r = H.run("#runover prim\nif (@P.z > 0.5) extrude(0.3);")
        self.assertGreater(r.count("mesh", "FACE"), 20)
        self.assertTrue(any(r.attr("extrudeFront")))
        H.clear()
        r = H.run("bevel(0.05, 2);")
        self.assertGreater(r.count("mesh", "FACE"), 20)
        H.clear()
        r = H.run("subdivsurf(1);")
        self.assertEqual(r.count("mesh", "FACE"), 60)

    def test_points_and_grid(self):
        r = H.run("points(10);\n@P = set(@ptnum, 0, 0);")
        self.assertEqual(r.count("pointcloud"), 10)
        self.assertTrue(H.close(r.attr("position", "pointcloud")[9], (9, 0, 0)))
        H.clear()
        r = H.run("grid(2, 2, 3, 3);\n@P.z = @P.x;")
        self.assertEqual(r.count(), 9)

    def test_transform(self):
        pts, ob = _ico_points()
        r = H.run("transform({0, 0, 1}, {0, 0, 0}, 2);", ob=ob)
        self.assertTrue(all(H.close(a, (2 * p[0], 2 * p[1], 2 * p[2] + 1)) for a, p in zip(r.positions(), pts)))

    def test_old_locals_after_scatter_are_an_error(self):
        with self.assertRaises(FormulaError) as cm:
            compiler.compile_source("float h = @P.z;\nscatter(10);\nf@h = h;")
        self.assertIn("scatter()", str(cm.exception))


@unittest.skipUnless(HAVE_BPY, "needs Blender's bpy module")
class TestRampsAndInterface(unittest.TestCase):
    def setUp(self):
        H.clear()

    def test_chramp_preset_and_colour(self):
        r = H.run('f@r = chramp("profile", fit(@P.z, -1, 1, 0, 1), "bell");\n@Cd = chramp("col", f@r);')
        pts = [tuple(v.co) for v in r.ob.data.vertices]
        top = max(range(12), key=lambda i: pts[i][2])
        self.assertTrue(H.close(r.attr("r")[top], 0.0, 1e-3))
        mid = [i for i in range(12) if abs(pts[i][2]) < 0.5]
        self.assertTrue(all(r.attr("r")[i] > 0.5 for i in mid))
        self.assertTrue(H.close(r.attr("Cd")[top][:3], (0, 0, 0), 1e-3))

    def test_edited_ramp_survives_rebuild(self):
        src = 'f@r = chramp("profile", @P.z);'
        r = H.run(src)
        node = next(n for n in r.tree.nodes if n.get("ftn_ramp") == "profile")
        node.mapping.curves[0].points[1].location = (1.0, 0.25)
        node.mapping.update()
        res = compiler.compile_source(src + "\nf@other = 1;")
        build.build_group(res, src, "SCRIPT", target=r.tree)
        node = next(n for n in r.tree.nodes if n.get("ftn_ramp") == "profile")
        self.assertTrue(H.close(tuple(node.mapping.curves[0].points[1].location), (1.0, 0.25)))

    def test_parameter_panels_and_tooltips(self):
        r = H.run('f@a = chf("Shape/amp", 0.5, tip="height");\nf@b = chf("Shape/freq", 2);\nf@c = chf("other", 1);')
        items = [(it.item_type, it.name, it.parent.name if it.parent else "") for it in r.tree.interface.items_tree]
        self.assertIn(("PANEL", "Shape", ""), items)
        self.assertIn(("SOCKET", "amp", "Shape"), items)
        self.assertIn(("SOCKET", "freq", "Shape"), items)
        amp = next(it for it in r.tree.interface.items_tree if it.name == "amp")
        self.assertEqual(amp.description, "height")
        self.assertTrue(H.close(r.attr("a")[0], 0.5))

    def test_rebuild_keeps_modifier_values(self):
        src = 'f@a = chf("amp", 0.5);'
        r = H.run(src, params={"amp": 3.0})
        res = compiler.compile_source('f@a = chf("amp", 0.5) + chf("Extra/b", 1);')
        build.build_group(res, src, "SCRIPT", target=r.tree)
        self.assertTrue(H.close(H.get_input(r.mod, r.tree, "amp"), 3.0))


@unittest.skipUnless(HAVE_BPY, "needs Blender's bpy module")
class TestSimulation(unittest.TestCase):
    def test_simulation_carries_single_values(self):
        H.clear()
        scene = bpy.context.scene
        scene.frame_start = 1
        scene.frame_set(1)
        r = H.run("float t = 0;\nsimulate { t += 1; f@t = t; }")
        for f in (2, 3, 4):
            scene.frame_set(f)
        dg = bpy.context.evaluated_depsgraph_get()
        geo = r.ob.evaluated_get(dg).evaluated_geometry()
        vals = [d.value for d in geo.mesh.attributes["t"].data]
        self.assertTrue(all(H.close(v, 4) for v in vals), vals[:3])
        scene.frame_set(1)


if __name__ == "__main__":
    unittest.main()
