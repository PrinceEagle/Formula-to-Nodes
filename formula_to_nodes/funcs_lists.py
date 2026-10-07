# SPDX-License-Identifier: GPL-3.0-or-later
"""Arrays, built on Blender's lists (5.2+).

A Blender list is one value for the whole geometry (like a detail
attribute), but any element can index into it: palettes, lookup tables,
weights. Per-element lists (one array per point) aren't possible."""

import ast

from .core import (FLOAT, INT, BOOL, VECTOR, STRING, ROTATION, MATRIX, SOCKET_DTYPE, Val, FUNCS, reg,
                   FormulaError, type_word, is_list, elem_type, list_type)

reg("array", "array(a, b, ...)", "builds an array (all values must be the same for every element)", "Arrays",
    "f_array", feature="lists")
reg("len", "len(array or text or vector)", "number of items / characters (3 for a vector)", "Arrays", "f_len")
reg("append push", "NAME(array, value)", "adds a value at the end (changes the array variable)", "Arrays",
    "f_append", feature="lists", effect=True, expr_ok=True)
reg("insert", "insert(array, index, value)", "inserts a value (changes the array variable)", "Arrays",
    "f_insert", feature="lists", effect=True, expr_ok=True)
reg("removeindex", "removeindex(array, index)", "removes one item (changes the array variable)", "Arrays",
    "f_removeindex", feature="lists", effect=True, expr_ok=True)
reg("resize", "resize(array, length)", "grows (repeating nothing: zeros) or shrinks an array", "Arrays",
    "f_resize", feature="lists", effect=True, expr_ok=True)
reg("sort", "sort(array)", "sorted copy (numbers)", "Arrays", "f_sort", feature="lists")
reg("sum", "sum(array or vector)", "sum of the items (or the x + y + z of a vector)", "Arrays", "f_sum")


class ListFuncs:
    def _index_node(self):
        return Val(INT, o=self.g.add("GeometryNodeInputIndex").out("Index"), field=True)

    def _field_to_list(self, count, value, et):
        """List of ``count`` items where item i = ``value`` evaluated with Index = i."""
        n = self.g.add("GeometryNodeFieldToList", {},
                       {"Count": self.inp(count, INT), "Field_0": self.inp(value, et)},
                       items=[(SOCKET_DTYPE[et], "Value")], items_attr="list_items")
        static = int(count.c) if count.is_const else None
        return Val(list_type(et), c=static, o=n.out("List_0"))

    def array_value(self, node, et, what):
        """An array from a {a, b, c} literal, {} / nothing (empty) or an
        expression of array type."""
        self.require("lists")
        if et not in SOCKET_DTYPE or et in ("GEOMETRY",):
            raise FormulaError(f"arrays of {type_word(et)} aren't supported")
        if node is None or (isinstance(node, ast.Dict) and not node.keys):
            return self._field_to_list(Val(INT, c=0), Val(et, c=self._zero_c(et)), et)
        if isinstance(node, ast.Set):
            items = [self.coerce(self.expr(e) if not isinstance(e, ast.Set) else self.expr_hint(e, et), et,
                                 f"an item of {what}") for e in node.elts]
            return self.list_literal(items, et, what)
        v = self.expr(node)
        if not is_list(v.t):
            raise FormulaError(f"{what} must be an array, e.g. {{1, 2, 3}} or array(1, 2, 3)")
        return self.coerce(v, list_type(et), what)

    def _zero_c(self, et):
        return {FLOAT: 0.0, INT: 0, BOOL: False, VECTOR: (0.0, 0.0, 0.0), STRING: "",
                ROTATION: (0.0, 0.0, 0.0)}.get(et, 0.0)

    def list_literal(self, items, et, what="the array"):
        for v in items:
            if v.field:
                raise FormulaError(f"{what}: array items must be the same for every element (an array is one "
                                   f"value for the whole geometry) — this one differs per element")
        n = len(items)
        if n == 0:
            return self._field_to_list(Val(INT, c=0), Val(et, c=self._zero_c(et)), et)
        if n == 1:
            value = items[0]
        else:
            value = self.index_switch(self._index_node(), items, et)
        return self._field_to_list(Val(INT, c=n), value, et)

    def f_array(self, node, name):
        hint = self.hinted(node)
        vals = [self.expr(a) for a in node.args]
        if hint is not None and is_list(hint):
            et = elem_type(hint)
        elif not vals:
            et = FLOAT
        else:
            ts = {v.t for v in vals}
            if len(ts) == 1:
                et = ts.pop()
            elif ts <= {FLOAT, INT, BOOL}:
                et = FLOAT
            elif ts <= {FLOAT, INT, BOOL, VECTOR}:
                et = VECTOR
            else:
                raise FormulaError("array() items must all have the same type")
        return self.list_literal([self.coerce(v, et, "an array item") for v in vals], et)

    # ── access ─────────────────────────────────────────────────────────────
    def list_len(self, arr):
        if isinstance(arr.c, int):
            return Val(INT, c=arr.c)
        n = self.g.add("GeometryNodeListLength", {"data_type": SOCKET_DTYPE[elem_type(arr.t)]}, {"List": arr.o})
        return Val(INT, o=n.out("Length"))

    def list_get(self, arr, idx):
        et = elem_type(arr.t)
        if idx.is_const and idx.c < 0:
            idx = self.coerce(self.math("ADD", self.list_len(arr), idx), INT)
        n = self.g.add("GeometryNodeListGetItem", {"socket_type": SOCKET_DTYPE[et]},
                       {"List": arr.o, "Index": self.inp(idx, INT)})
        return Val(et, o=n.out("Value"), field=idx.field or arr.field)

    def list_set(self, arr, idx, value):
        et = elem_type(arr.t)
        value = self.coerce(value, et, "the array item")
        if idx.field or value.field:
            raise FormulaError("array items can only be set to values that are the same for every element")
        i = self._index_node()
        cur = self.list_get(arr, i)
        new = self.switch(self.compare("EQUAL", i, idx), cur, value, et)
        return self._field_to_list(self.list_len(arr), new, et)

    def list_slice(self, arr, sl):
        if sl.step is not None:
            raise FormulaError("array slices can't have a step")
        n = self.list_len(arr)
        start = self.coerce(self.expr(sl.lower), INT, "the slice start") if sl.lower else Val(INT, c=0)
        end = self.coerce(self.expr(sl.upper), INT, "the slice end") if sl.upper else n
        for v in (start, end):
            if v.field:
                raise FormulaError("array slices need the same bounds for every element")
        if start.is_const and start.c < 0:
            start = self.coerce(self.math("ADD", n, start), INT)
        if end.is_const and end.c < 0:
            end = self.coerce(self.math("ADD", n, end), INT)
        count = self.coerce(self.math("MAXIMUM", self.math("SUBTRACT", end, start), Val(FLOAT, c=0.0)), INT)
        item = self.list_get(arr, self.coerce(self.math("ADD", self._index_node(), start), INT))
        return self._field_to_list(count, item, elem_type(arr.t))

    def list_reverse(self, arr):
        n = self.list_len(arr)
        j = self.coerce(self.math("SUBTRACT", self.math("SUBTRACT", n, Val(FLOAT, c=1.0)), self._index_node()), INT)
        return self._field_to_list(n, self.list_get(arr, j), elem_type(arr.t))

    def list_convert(self, arr, et):
        item = self.coerce(self.list_get(arr, self._index_node()), et, "an array item")
        return self._field_to_list(self.list_len(arr), item, et)

    def _points_for(self, arr):
        """A throw-away point cloud with one point per item, so geometry
        statistics can reduce the list."""
        p = self.g.add("GeometryNodePoints", {}, {"Count": self.inp(self.list_len(arr), INT)})
        return p.out("Geometry")

    def list_reduce(self, arr, out):
        et = elem_type(arr.t)
        if et not in (FLOAT, INT, BOOL, VECTOR):
            raise FormulaError(f"can't reduce an array of {type_word(et)}")
        vec = et == VECTOR
        item = self.coerce(self.list_get(arr, self._index_node()), VECTOR if vec else FLOAT)
        st = self.g.add("GeometryNodeAttributeStatistic", {"data_type": "FLOAT_VECTOR" if vec else "FLOAT",
                                                           "domain": "POINT"},
                        {"Geometry": self._points_for(arr), "Attribute": self.inp(item, VECTOR if vec else FLOAT)})
        t = VECTOR if vec else (et if et == INT and out in ("Min", "Max", "Sum") else FLOAT)
        return Val(t, o=st.out(out))

    def list_find(self, arr, value):
        et = elem_type(arr.t)
        value = self.coerce(value, et, "the value to find")
        i = self._index_node()
        hit = self.compare("EQUAL", self.list_get(arr, i), value)
        big = Val(FLOAT, c=float(1 << 24))
        cand = self.switch(hit, big, self.coerce(i, FLOAT), FLOAT)
        st = self.g.add("GeometryNodeAttributeStatistic", {"data_type": "FLOAT", "domain": "POINT"},
                        {"Geometry": self._points_for(arr), "Attribute": self.inp(cand, FLOAT)})
        first = Val(FLOAT, o=st.out("Min"), field=value.field)
        return self.coerce(self.switch(self.compare("GREATER_EQUAL", first, big), first, Val(FLOAT, c=-1.0), FLOAT), INT)

    def list_math(self, op, l, r):
        """Math nodes work on lists item by item (Blender 5.2+)."""
        lt = elem_type(l.t) if is_list(l.t) else l.t
        rt = elem_type(r.t) if is_list(r.t) else r.t
        if lt not in (FLOAT, INT, BOOL, VECTOR) or rt not in (FLOAT, INT, BOOL, VECTOR):
            raise FormulaError("only arrays of numbers or vectors work with + - * /")
        if l.field or r.field:
            raise FormulaError("array math needs values that are the same for every element")
        opname = self._OPNAME[op]
        static = l.c if is_list(l.t) else r.c
        if VECTOR in (lt, rt):
            ins = {}
            for k, (v, t) in enumerate(((l, lt), (r, rt))):
                ins[("Vector", "Vector_001")[k]] = v.o if is_list(v.t) else self.inp(v, VECTOR)
            if op is ast.Mult and FLOAT in (lt, rt) and VECTOR in (lt, rt):
                vec, sc = (l, r) if lt == VECTOR else (r, l)
                n = self.g.add("ShaderNodeVectorMath", {"operation": "SCALE"},
                               {"Vector": vec.o if is_list(vec.t) else self.inp(vec, VECTOR),
                                "Scale": sc.o if is_list(sc.t) else self.inp(sc, FLOAT)})
            else:
                n = self.g.add("ShaderNodeVectorMath", {"operation": opname}, ins)
            return Val(list_type(VECTOR), c=static, o=n.out("Vector"))
        ins = {"Value": l.o if is_list(l.t) else self.inp(l, FLOAT),
               "Value_001": r.o if is_list(r.t) else self.inp(r, FLOAT)}
        n = self.g.add("ShaderNodeMath", {"operation": opname}, ins)
        return Val(list_type(FLOAT), c=static, o=n.out("Value"))

    # ── functions ──────────────────────────────────────────────────────────
    def f_len(self, node, name):
        (v,) = self.args(node, name, {1})
        if is_list(v.t):
            return self.list_len(v)
        if v.t == STRING:
            return self.f_strlen_val(v)
        if v.t == VECTOR:
            return Val(INT, c=3)
        if v.t == ROTATION:
            return Val(INT, c=4)
        raise FormulaError(f"len() needs an array, text or a vector, not a {type_word(v.t)}")

    def _arr_arg(self, node, name):
        arr = self.expr(node.args[0])
        if not is_list(arr.t):
            raise FormulaError(f"{name}()'s first argument must be an array")
        if arr.field:
            raise FormulaError("arrays are the same for every element")
        return arr

    def _store_back(self, node, arr, name):
        if isinstance(node.args[0], ast.Name):
            self.assign_out(node.args[0], arr, name)
        return arr

    def f_append(self, node, name):
        if len(node.args) != 2:
            raise FormulaError(f"{name}() takes {name}(array, value)")
        arr = self._arr_arg(node, name)
        et = elem_type(arr.t)
        value = self.coerce(self.expr(node.args[1]), et, f"{name}()'s value")
        if value.field:
            raise FormulaError("array items must be the same for every element")
        n = self.list_len(arr)
        i = self._index_node()
        item = self.switch(self.compare("LESS_THAN", i, n), value, self.list_get(arr, i), et)
        new = self._field_to_list(self.coerce(self.math("ADD", n, Val(FLOAT, c=1.0)), INT), item, et)
        return self._store_back(node, new, name)

    def f_insert(self, node, name):
        if len(node.args) != 3:
            raise FormulaError("insert() takes insert(array, index, value)")
        arr = self._arr_arg(node, name)
        et = elem_type(arr.t)
        at = self.coerce(self.expr(node.args[1]), INT, "insert()'s index")
        value = self.coerce(self.expr(node.args[2]), et, "insert()'s value")
        if at.field or value.field:
            raise FormulaError("array items must be the same for every element")
        n = self.list_len(arr)
        i = self._index_node()
        shifted = self.list_get(arr, self.coerce(self.math("SUBTRACT", i, Val(FLOAT, c=1.0)), INT))
        before = self.list_get(arr, i)
        item = self.switch(self.compare("LESS_THAN", i, at),
                           self.switch(self.compare("EQUAL", i, at), shifted, value, et), before, et)
        new = self._field_to_list(self.coerce(self.math("ADD", n, Val(FLOAT, c=1.0)), INT), item, et)
        return self._store_back(node, new, name)

    def f_removeindex(self, node, name):
        if len(node.args) != 2:
            raise FormulaError("removeindex() takes removeindex(array, index)")
        arr = self._arr_arg(node, name)
        et = elem_type(arr.t)
        at = self.coerce(self.expr(node.args[1]), INT, "removeindex()'s index")
        if at.field:
            raise FormulaError("the index must be the same for every element")
        n = self.list_len(arr)
        i = self._index_node()
        src = self.switch(self.compare("LESS_THAN", i, at), self.coerce(self.math("ADD", i, Val(FLOAT, c=1.0)), INT),
                          i, INT)
        new = self._field_to_list(self.coerce(self.math("MAXIMUM", self.math("SUBTRACT", n, Val(FLOAT, c=1.0)),
                                                        Val(FLOAT, c=0.0)), INT), self.list_get(arr, src), et)
        return self._store_back(node, new, name)

    def f_resize(self, node, name):
        if len(node.args) != 2:
            raise FormulaError("resize() takes resize(array, length)")
        arr = self._arr_arg(node, name)
        count = self.coerce(self.expr(node.args[1]), INT, "resize()'s length")
        if count.field:
            raise FormulaError("the length must be the same for every element")
        new = self._field_to_list(count, self.list_get(arr, self._index_node()), elem_type(arr.t))
        return self._store_back(node, new, name)

    def f_sort(self, node, name):
        (arr,) = self.args(node, name, {1})
        if not is_list(arr.t) or elem_type(arr.t) not in (FLOAT, INT):
            raise FormulaError("sort() sorts arrays of numbers")
        et = elem_type(arr.t)
        weight = self.coerce(self.list_get(arr, self._index_node()), FLOAT)
        n = self.g.add("GeometryNodeSortList", {"socket_type": SOCKET_DTYPE[et]},
                       {"List": arr.o, "Sort Weight": self.inp(weight, FLOAT)})
        return Val(arr.t, c=arr.c, o=n.out("List"))

    def f_sum(self, node, name):
        (v,) = self.args(node, name, {1})
        if is_list(v.t):
            return self.list_reduce(v, "Sum")
        if v.t == VECTOR:
            return self.math("ADD", self.math("ADD", self.sep(v, 0), self.sep(v, 1)), self.sep(v, 2))
        return v
