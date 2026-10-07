# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure-Python tests: parser, compiler, capability gating, includes, AI prompt
and the MCP server protocol. No Blender needed:
    python -m unittest discover -s tests -p "test_compiler.py"
"""

import io
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from formula_to_nodes import ai, caps, compiler, examples, lang, libs  # noqa: E402
from formula_to_nodes.lang import FormulaError  # noqa: E402


def ok(src, **kw):
    return compiler.compile_source(src, **kw)


class TestParser(unittest.TestCase):
    def test_directives(self):
        prog = lang.parse_source("#runover prim\n#define AMP 0.5\n// # define is just a comment\n@P.z += AMP;")
        self.assertEqual(prog.runover, "FACE")
        self.assertEqual(prog.defines, {"AMP": "(0.5)"})
        self.assertEqual(len(prog.stmts), 1)

    def test_runover_twice_is_an_error(self):
        with self.assertRaises(FormulaError):
            lang.parse_source("#runover prim\n#runover point\nf@x = 1;")

    def test_functions_and_overloads(self):
        prog = lang.parse_source("float f(float a) { return a; }\nfloat f(float a; float b) { return a + b; }\n"
                                 "vector g(vector p, q; float t) { return p; }\nf@x = f(1);")
        self.assertEqual([len(f.params) for f in prog.functions["f"]], [1, 2])
        self.assertEqual(prog.functions["g"][0].params, [("VECTOR", "p"), ("VECTOR", "q"), ("FLOAT", "t")])

    def test_loops(self):
        prog = lang.parse_source("for (int i = 0; i < 3; i++) { f@x += i; }\n"
                                 "foreach (float v; vals) f@y += v;\nfor (int j = 0; j < 2; j++) f@z += j;")
        kinds = [type(s).__name__ for s in prog.stmts]
        self.assertEqual(kinds, ["SFor", "SForeachArr", "SFor"])

    def test_while_becomes_a_bounded_repeat(self):
        prog = lang.parse_source("while (x > 0) { x -= 1; }")
        self.assertEqual(prog.stmts[0].kind, "repeat")

    def test_do_while_is_rejected_with_a_hint(self):
        with self.assertRaises(FormulaError) as cm:
            lang.parse_source("do { x -= 1; } while (x > 0);")
        self.assertIn("while (condition)", str(cm.exception))

    def test_declarations(self):
        prog = lang.parse_source("float a = 1, b = max(2, 3);\nint ids[] = {1, 2};\nvector4 q = {0, 0, 0, 1};\n"
                                 "string s = \"a, b\";\nmatrix m = ident();")
        types = [(s.name, s.vtype) for s in prog.stmts]
        self.assertEqual(types, [("a", "FLOAT"), ("b", "FLOAT"), ("ids", "LIST:INT"), ("q", "ROTATION"),
                                 ("s", "STRING"), ("m", "MATRIX")])

    def test_include_errors_point_at_the_include(self):
        with self.assertRaises(FormulaError) as cm:
            lang.parse_source('#include "lib.h"\nf@x = 1;', resolver=lambda n: "float f(float x) { return x * ; }")
        self.assertEqual(cm.exception.source, "lib.h")

    def test_include_cycles(self):
        texts = {"a.h": '#include "b.h"\n', "b.h": '#include "a.h"\n'}
        with self.assertRaises(FormulaError) as cm:
            lang.parse_source('#include "a.h"\nf@x = 1;', resolver=texts.get)
        self.assertIn("includes itself", str(cm.exception))


class TestExamplesAndCompat(unittest.TestCase):
    # node counts of the 2.1.0 compiler: the rewrite must not grow old scripts
    BASELINE = {"moss": 8, "wave": 14, "noise_disp": 14, "twist": 8, "flatten": 12, "rest": 10,
                "radial": 10, "pscale": 10, "grow": 9, "spiral": 10, "faces": 10, "side": 7}

    def test_examples_compile(self):
        for key, title, desc, src in examples.EXAMPLES:
            with self.subTest(key):
                res = ok(src)
                if key in self.BASELINE:
                    self.assertLessEqual(res.node_count, self.BASELINE[key])

    def test_strict_mode_needs_prefixes(self):
        with self.assertRaises(FormulaError):
            compiler.compile_source("@mask = 1;", strict=True)


class TestErrorsAndNotes(unittest.TestCase):
    def assertErr(self, src, needle, **kw):
        with self.assertRaises(FormulaError) as cm:
            compiler.compile_source(src, **kw)
        self.assertIn(needle, str(cm.exception))
        return cm.exception

    def test_line_numbers(self):
        e = self.assertErr("f@a = 1;\nf@b = nosuch(2);", "unknown function")
        self.assertEqual(e.line, 2)

    def test_function_errors_name_the_function(self):
        self.assertErr("float f(float x) { return y; }\nf@a = f(1);", "in f()")

    def test_recursion(self):
        self.assertErr("float f(float x) { return f(x); }\nf@a = f(1);", "recursion")

    def test_missing_return(self):
        self.assertErr("float f(float x) { float y = x; }\nf@a = f(1);", "return")

    def test_loop_variable_changed(self):
        self.assertErr("for (int i = 0; i < 3; i++) { i = 5; }\nf@a = 1;", "loop variable")

    def test_per_point_loop_bound(self):
        self.assertErr("float s = 0;\nfor (int i = 0; i < @ptnum; i++) { s += 1; }\nf@a = s;", "same for every")

    def test_infinite_loop(self):
        self.assertErr("for (int i = 0; i < 3; i--) { f@a = i; }", "never ends")

    def test_effect_in_expression(self):
        self.assertErr("f@a = scatter(3);", "statement")

    def test_detail_needs_single_values(self):
        self.assertErr("#runover detail\nf@a = @P.z;", "aggregate")

    def test_position_in_prim_mode(self):
        self.assertErr("#runover prim\n@P += 1;", "points")

    def test_ptnum_in_prim_mode(self):
        self.assertErr("#runover prim\ni@x = @ptnum;", "@primnum")

    def test_geometry_op_in_if(self):
        self.assertErr("if (@P.z > 0) points(3);", "if")

    def test_old_local_after_scatter(self):
        self.assertErr("float h = @P.z;\nscatter(5);\nf@h = h;", "scatter()")

    def test_string_attribute_gate(self):
        self.assertErr('s@name = "x";', "5.3", target=(5, 2, 0))
        ok('s@name = "x";', target=(5, 3, 0))

    def test_array_items_must_be_single(self):
        self.assertErr("float a[] = {@P.z};\nf@x = a[0];", "same for every element")

    def test_unsupported_hint(self):
        self.assertErr("int n[] = neighbours(0, @ptnum);\nf@x = 1;", "neighbour")
        self.assertErr("i[]@n = {1, 2};", "array attributes")

    def test_typo_suggestion(self):
        self.assertErr("f@x = smoothstp(0, 1, @P.z);", "smoothstep")

    def test_printf_needs_single_values(self):
        self.assertErr('printf("%g", @P.z);', "single values")

    def test_notes(self):
        res = ok("@mask = 1;")
        self.assertTrue(any("type prefix" in n for n in res.notes))

    def test_unused_parameter_note(self):
        res = ok('float unused = chf("unused", 1);\nf@x = 1;')
        self.assertTrue(any("isn't used" in n for n in res.notes))


class TestFeatures(unittest.TestCase):
    def test_runover_argument_and_directive(self):
        self.assertEqual(ok("f@x = 1;", runover="prim").runover, "FACE")
        self.assertEqual(ok("#runover edge\nf@x = 1;", runover="prim").runover, "EDGE")

    def test_unrolled_loop_folds_to_constant(self):
        res = ok("float s = 0;\nfor (int i = 0; i < 4; i++) s += i;\nf@s = s;")
        stores = [n for n in res.graph.nodes if n.idname == "GeometryNodeStoreNamedAttribute"]
        self.assertEqual(stores[0].inputs["Value"], 6.0)

    def test_long_loop_becomes_a_repeat_zone(self):
        res = ok("float s = 0;\nfor (int i = 0; i < 100; i++) s += i;\nf@s = s;")
        self.assertTrue(any(n.idname == "GeometryNodeRepeatInput" for n in res.graph.nodes))

    def test_functions_inline_and_fold(self):
        res = ok("float sq(float x) { return x * x; }\nf@a = sq(3);")
        stores = [n for n in res.graph.nodes if n.idname == "GeometryNodeStoreNamedAttribute"]
        self.assertEqual(stores[0].inputs["Value"], 9.0)

    def test_param_panels(self):
        res = ok('f@a = chf("Shape/amp", 0.5, min=0, max=2, tip="Height");')
        p = next(s for s in res.iface if s.name == "amp")
        self.assertEqual((p.panel, p.min, p.max, p.description), ("Shape", 0.0, 2.0, "Height"))

    def test_resource_params(self):
        res = ok('instance(chcoll("Rocks"), int(rand(@ptnum) * 3));')
        self.assertTrue(any(s.vtype == "COLLECTION" for s in res.iface))

    def test_ramps_listed(self):
        res = ok('f@r = chramp("profile", @P.z, "bell");\nvector c = chramp("tint", f@r);\n@Cd = c;')
        self.assertEqual(res.ramps, [("profile", "float"), ("tint", "color")])

    def test_bundled_includes(self):
        for name, _ in libs.bundled():
            with self.subTest(name):
                ok(f'#include "{name}"\nf@x = 1;')
        ok('#include "falloff.h"\n#include "sdf.h"\n#include "shaping.h"\n#include "color.h"\n#include "noise.h"\n'
           'f@a = falloff_sphere(v@P, {0, 0, 0}, 1, 0.5) * pulse(@P.z, 0, 0.2);\nf@b = sdtorus(v@P, 1, 0.25);\n'
           '@Cd = palette_sunset(f@a);\nf@h = terrain(v@P, 2, 1) + marble(v@P, 3, 2);')

    def test_capability_report(self):
        t = caps.Target((5, 2, 0))
        rep = dict((k, okk) for k, okk, _ in caps.report(t))
        self.assertTrue(rep["lists"])
        self.assertFalse(rep["string_fields"])
        self.assertTrue(dict((k, okk) for k, okk, _ in caps.report(caps.Target((5, 3, 0))))["string_fields"])

    def test_missing_node_types_are_reported(self):
        t = caps.Target((5, 2, 0), node_types={"GeometryNodeMeshBevel"})
        with self.assertRaises(FormulaError) as cm:
            compiler.compile_source("float a[] = {1, 2};\nf@x = a[0];", target=t)
        self.assertIn("isn't available", str(cm.exception))

    def test_reference_filters_by_target(self):
        ref52 = compiler.reference_by_category((5, 2, 0))
        names = {fd.name for fds in ref52.values() for fd in fds}
        self.assertIn("bevel", names)


class TestAIPrompt(unittest.TestCase):
    def test_prompt_mentions_new_features(self):
        p = ai.build_system_prompt()
        for needle in ("#runover", "removepoint", "chramp", "scatter", "quaternion", "for (int"):
            self.assertIn(needle, p)

    def test_prompt_examples_compile(self):
        for key in examples.AI_FEW_SHOT:
            ok(examples.get(key)[3])

    def test_default_models_are_current(self):
        self.assertTrue(ai.PROVIDERS["ANTHROPIC"]["model"].startswith("claude-"))
        self.assertNotEqual(ai.PROVIDERS["ANTHROPIC"]["model"], "claude-sonnet-5")

    def test_job_retries_until_it_compiles(self):
        replies = iter(["```\nf@x = nosuch(1);\n```", "```\nf@x = 1;\n```"])
        cfg = ai.Config("OLLAMA", model="m", url="http://localhost:1/v1/chat/completions")
        job = ai.Job(cfg, "make x", send=lambda c, s, m: next(replies))
        job.run()
        self.assertEqual(job.script, "f@x = 1;")
        self.assertIsNone(job.error)
        self.assertEqual(job.attempt, 2)


class TestMCPServer(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, os.path.join(ROOT, "formula_to_nodes"))
        import mcp_server
        self.mcp = mcp_server

    def call(self, server, method, params=None, mid=1):
        out = io.BytesIO()
        server.out = out
        msg = {"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}
        server.serve(io.BytesIO((json.dumps(msg) + "\n").encode()))
        return json.loads(out.getvalue().decode().strip().splitlines()[-1])

    def test_tools_list_and_check(self):
        server = self.mcp.Server(self.mcp.Bridge("127.0.0.1", 1))
        tools = {t["name"] for t in self.call(server, "tools/list")["result"]["tools"]}
        for name in ("formula_check_script", "formula_language_guide", "formula_list_recipes",
                     "formula_capabilities"):
            self.assertIn(name, tools)
        res = self.call(server, "tools/call", {"name": "formula_check_script",
                                               "arguments": {"script": "f@x = chf(\"a\", 1);"}})
        data = json.loads(res["result"]["content"][0]["text"])
        self.assertTrue(data["ok"])
        res = self.call(server, "tools/call", {"name": "formula_check_script",
                                               "arguments": {"script": "f@x = nosuch(1);"}})
        self.assertTrue(res["result"]["isError"])

    def test_recipes_tool(self):
        server = self.mcp.Server(self.mcp.Bridge("127.0.0.1", 1))
        res = self.call(server, "tools/call", {"name": "formula_list_recipes", "arguments": {}})
        data = json.loads(res["result"]["content"][0]["text"])
        self.assertGreater(len(data["recipes"]), 20)


if __name__ == "__main__":
    unittest.main()
