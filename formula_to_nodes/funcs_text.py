# SPDX-License-Identifier: GPL-3.0-or-later
"""Text (string) functions and messages (printf / warning / error).

Single text values work in Blender 5.2; text that differs per element
(string fields, s@ attributes) needs Blender 5.3."""

import ast
import re

from .core import (FLOAT, INT, BOOL, VECTOR, STRING, VOID, Val, FUNCS, reg, FormulaError, type_word,
                   list_type)

reg("sprintf", "sprintf(format, ...)", 'formats text like C: sprintf("piece_%03d", i), %d %f %.2f %g %s %x',
    "Text & messages", "f_sprintf")
reg("printf", "printf(format, ...)", "shows a message on the node and the modifier (values must be single)",
    "Text & messages", "f_message", effect=True, level="INFO")
reg("warning", "warning(format, ...)", "shows a warning (if any element hits it, inside an if)",
    "Text & messages", "f_message", effect=True, level="WARNING")
reg("error", "error(format, ...)", "shows an error message", "Text & messages", "f_message", effect=True,
    level="ERROR")
reg("concat", "concat(a, b, ...)", "joins text (also a + b)", "Text & messages", "f_concat")
reg("strlen", "strlen(s)", "number of characters", "Text & messages", "f_strlen")
reg("toupper", "toupper(s)", "UPPER CASE", "Text & messages", "f_case", case="Uppercase")
reg("tolower", "tolower(s)", "lower case", "Text & messages", "f_case", case="Lowercase")
reg("strip", "strip(s [, chars])", "removes whitespace (or chars) at both ends", "Text & messages", "f_strip",
    feature="string_tools", start=True, end=True)
reg("lstrip", "lstrip(s [, chars])", "removes whitespace (or chars) at the start", "Text & messages", "f_strip",
    feature="string_tools", start=True, end=False)
reg("rstrip", "rstrip(s [, chars])", "removes whitespace (or chars) at the end", "Text & messages", "f_strip",
    feature="string_tools", start=False, end=True)
reg("replace", "replace(s, find, with)", "replaces every occurrence", "Text & messages", "f_replace")
reg("reverse", "reverse(s or array)", "text or array in reverse order", "Text & messages", "f_reverse")
reg("find", "find(s, sub) or find(array, value)", "first index of sub/value, -1 if missing", "Text & messages", "f_find")
reg("rfind", "rfind(s, sub)", "last index of sub, -1 if missing", "Text & messages", "f_find", from_end=True)
reg("startswith", "startswith(s, prefix)", "does s start with prefix", "Text & messages", "f_matchstr", op="Starts With")
reg("endswith", "endswith(s, suffix)", "does s end with suffix", "Text & messages", "f_matchstr", op="Ends With")
reg("contains", "contains(s, sub)", "does s contain sub", "Text & messages", "f_matchstr", op="Contains")
reg("match", 'match(pattern, s)', 'glob match with * at the start and/or end: match("rock*", name)',
    "Text & messages", "f_match")
reg("atof", "atof(s)", "text to float", "Text & messages", "f_atonum", t=FLOAT)
reg("atoi", "atoi(s)", "text to int", "Text & messages", "f_atonum", t=INT)
reg("itoa", "itoa(i)", "int to text", "Text & messages", "f_itoa")
reg("split", "split(s [, separator])", "text → array of text pieces", "Text & messages", "f_split", feature="lists")

_FMT_RE = re.compile(r"%(?P<flags>[-+ 0#]*)(?P<width>\d+)?(?:\.(?P<prec>\d+))?(?P<conv>[diufFgGeExXsc%v])")


class TextFuncs:
    def str_val(self, v, what):
        if v.t != STRING:
            raise FormulaError(f"{what} must be text, not a {type_word(v.t)} — use itoa() or sprintf()")
        if v.field:
            self.require("string_fields")
        return v

    def concat(self, vals):
        vals = [self.str_val(v, "concat()'s argument") for v in vals]
        if all(v.is_const for v in vals):
            return Val(STRING, c="".join(v.c for v in vals))
        n = self.g.add("GeometryNodeStringJoin", {}, {"Delimiter": "",
                                                      "Strings": [self.const_out(v).o if v.is_const else v.o
                                                                  for v in vals]})
        return Val(STRING, o=n.out("String"), field=any(v.field for v in vals))

    def f_concat(self, node, name):
        if not node.args:
            return Val(STRING, c="")
        return self.concat([self.expr(a) for a in node.args])

    def f_strlen(self, node, name):
        (s,) = self.args(node, name, {1})
        s = self.str_val(s, "strlen()'s argument")
        if s.is_const:
            return Val(INT, c=len(s.c))
        n = self.g.add("FunctionNodeStringLength", {}, {"String": s.o})
        return Val(INT, o=n.out("Length"), field=s.field)

    def f_case(self, node, name):
        (s,) = self.args(node, name, {1})
        s = self.str_val(s, f"{name}()'s argument")
        case = FUNCS[name].data["case"]
        if s.is_const:
            return Val(STRING, c=s.c.upper() if case == "Uppercase" else s.c.lower())
        n = self.g.add("FunctionNodeSetStringCase", {}, {"String": s.o, "Case": case})
        return Val(STRING, o=n.out("String"), field=s.field)

    def f_strip(self, node, name):
        vals = self.args(node, name, {1, 2})
        s = self.str_val(vals[0], f"{name}()'s argument")
        d = FUNCS[name].data
        chars = self.str_val(vals[1], f"{name}()'s characters") if len(vals) == 2 else Val(STRING, c="")
        if s.is_const and chars.is_const:
            c = chars.c or None
            return Val(STRING, c=s.c.strip(c) if d["start"] and d["end"] else
                       (s.c.lstrip(c) if d["start"] else s.c.rstrip(c)))
        n = self.g.add("FunctionNodeTrimString", {},
                       {"String": self.inp(s, STRING), "Characters": self.inp(chars, STRING),
                        "Whitespace": len(vals) == 1, "Start": d["start"], "End": d["end"]})
        return Val(STRING, o=n.out("String"), field=s.field or chars.field)

    def f_replace(self, node, name):
        s, f, r = [self.str_val(v, "replace()'s argument") for v in self.args(node, name, {3})]
        if s.is_const and f.is_const and r.is_const:
            return Val(STRING, c=s.c.replace(f.c, r.c) if f.c else s.c)
        n = self.g.add("FunctionNodeReplaceString", {},
                       {"String": self.inp(s, STRING), "Find": self.inp(f, STRING), "Replace": self.inp(r, STRING)})
        return Val(STRING, o=n.out("String"), field=s.field or f.field or r.field)

    def f_reverse(self, node, name):
        (v,) = self.args(node, name, {1})
        if v.t.startswith("LIST:"):
            return self.list_reverse(v)
        s = self.str_val(v, "reverse()'s argument")
        if s.is_const:
            return Val(STRING, c=s.c[::-1])
        self.require("string_tools")
        n = self.g.add("FunctionNodeReverseString", {}, {"String": s.o})
        return Val(STRING, o=n.out("String"), field=s.field)

    def f_find(self, node, name):
        a, b = self.args(node, name, {2})
        if a.t.startswith("LIST:"):
            return self.list_find(a, b)
        s, sub = self.str_val(a, f"{name}()'s text"), self.str_val(b, f"{name}()'s search text")
        from_end = FUNCS[name].data.get("from_end", False)
        if s.is_const and sub.is_const:
            return Val(INT, c=s.c.rfind(sub.c) if from_end else s.c.find(sub.c))
        n = self.g.add("FunctionNodeFindInString", {},
                       {"String": self.inp(s, STRING), "Search": self.inp(sub, STRING),
                        "Mode": "From End" if from_end else "From Start"})
        first = Val(INT, o=n.out("First Found"), field=s.field or sub.field)
        count = Val(INT, o=n.out("Count"), field=s.field or sub.field)
        return self.switch(self.compare("GREATER_THAN", count, Val(INT, c=0)), Val(INT, c=-1), first, INT)

    def _matchstr(self, s, key, op):
        if s.is_const and key.is_const:
            f = {"Starts With": str.startswith, "Ends With": str.endswith,
                 "Contains": lambda a, b: b in a}[op]
            return Val(BOOL, c=f(s.c, key.c))
        n = self.g.add("FunctionNodeMatchString", {},
                       {"String": self.inp(s, STRING), "Operation": op, "Key": self.inp(key, STRING)})
        return Val(BOOL, o=n.out("Result"), field=s.field or key.field)

    def f_matchstr(self, node, name):
        s, key = [self.str_val(v, f"{name}()'s argument") for v in self.args(node, name, {2})]
        return self._matchstr(s, key, FUNCS[name].data["op"])

    def f_match(self, node, name):
        if len(node.args) != 2:
            raise FormulaError('match() takes match(pattern, s)')
        pat = self.str_const(node.args[0], "match()'s pattern")
        s = self.str_val(self.expr(node.args[1]), "match()'s text")
        core = pat.strip("*")
        if any(c in core for c in "*?[]"):
            raise FormulaError("match() supports * only at the start and/or end of the pattern")
        if pat.startswith("*") and pat.endswith("*") and len(pat) > 1:
            return self._matchstr(s, Val(STRING, c=core), "Contains")
        if pat.endswith("*"):
            return self._matchstr(s, Val(STRING, c=core), "Starts With")
        if pat.startswith("*"):
            return self._matchstr(s, Val(STRING, c=core), "Ends With")
        return self.string_compare("EQUAL", s, Val(STRING, c=pat))

    def f_atonum(self, node, name):
        (s,) = self.args(node, name, {1})
        return self.string_to_number(self.str_val(s, f"{name}()'s argument"), FUNCS[name].data["t"])

    def string_to_number(self, s, t):
        if s.is_const:
            try:
                return Val(t, c=(int(float(s.c)) if t == INT else float(s.c)))
            except ValueError:
                return Val(t, c=0 if t == INT else 0.0)
        n = self.g.add("FunctionNodeStringToValue", {"data_type": "INT" if t == INT else "FLOAT"}, {"String": s.o})
        return Val(t, o=n.out("Value"), field=s.field)

    def f_itoa(self, node, name):
        (v,) = self.args(node, name, {1})
        i = self.coerce(v, INT, "itoa()'s argument")
        if i.is_const:
            return Val(STRING, c=str(int(i.c)))
        if i.field:
            self.require("string_fields")
        n = self.g.add("FunctionNodeValueToString", {"data_type": "INT"}, {"Value": i.o})
        return Val(STRING, o=n.out("String"), field=i.field)

    def f_split(self, node, name):
        vals = self.args(node, name, {1, 2})
        s = self.str_val(vals[0], "split()'s text")
        sep = self.str_val(vals[1], "split()'s separator") if len(vals) == 2 else Val(STRING, c=" ")
        n = self.g.add("FunctionNodeSplitString", {}, {"String": self.inp(s, STRING), "Separator": self.inp(sep, STRING)})
        static = None
        if s.is_const and sep.is_const and sep.c:
            static = len(s.c.split(sep.c))
        return Val(list_type(STRING), c=static, o=n.out("List"), field=s.field or sep.field)

    def string_index(self, s, idx):
        s = self.str_val(s, "the text")
        if isinstance(idx, ast.Slice):
            if idx.step is not None:
                raise FormulaError("text slices can't have a step")
            start = self.coerce(self.expr(idx.lower), INT, "the slice start") if idx.lower else Val(INT, c=0)
            if s.is_const and start.is_const and (idx.upper is None or self.expr(idx.upper).is_const):
                end = None if idx.upper is None else int(self.coerce(self.expr(idx.upper), INT).c)
                return Val(STRING, c=s.c[int(start.c):end])
            start = self._wrap_index(s, start)
            if idx.upper is None:
                length = Val(INT, c=1 << 20)
            else:
                end = self._wrap_index(s, self.coerce(self.expr(idx.upper), INT, "the slice end"))
                length = self.coerce(self.math("MAXIMUM", self.math("SUBTRACT", end, start), Val(FLOAT, c=0.0)), INT)
        else:
            start = self.coerce(self.expr(idx), INT, "the character index")
            if s.is_const and start.is_const:
                k = int(start.c)
                return Val(STRING, c=s.c[k] if -len(s.c) <= k < len(s.c) else "")
            start = self._wrap_index(s, start)
            length = Val(INT, c=1)
        n = self.g.add("FunctionNodeSliceString", {}, {"String": self.inp(s, STRING), "Position": self.inp(start, INT),
                                                       "Length": self.inp(length, INT)})
        return Val(STRING, o=n.out("String"), field=s.field or start.field or length.field)

    def _wrap_index(self, s, i):
        if i.is_const and i.c < 0:
            ln = self.f_strlen_val(s)
            return self.coerce(self.math("ADD", ln, i), INT)
        return i

    def f_strlen_val(self, s):
        if s.is_const:
            return Val(INT, c=len(s.c))
        n = self.g.add("FunctionNodeStringLength", {}, {"String": s.o})
        return Val(INT, o=n.out("Length"), field=s.field)

    def string_compare(self, op, a, b):
        if op not in ("EQUAL", "NOT_EQUAL"):
            raise FormulaError("text can only be compared with == or !=")
        if a.t != STRING or b.t != STRING:
            raise FormulaError("text can only be compared with text")
        if a.is_const and b.is_const:
            return Val(BOOL, c=(a.c == b.c) == (op == "EQUAL"))
        n = self.g.add("FunctionNodeCompare", {"data_type": "STRING", "operation": op},
                       {"A": self.inp(a, STRING), "B": self.inp(b, STRING)})
        return Val(BOOL, o=n.out("Result"), field=a.field or b.field)

    # ── formatting ─────────────────────────────────────────────────────────
    def format_text(self, node, fname, allow_fields):
        if not node.args:
            raise FormulaError(f"{fname}() needs a format text: {fname}(\"value: %g\", x)")
        fmt = self.str_const(node.args[0], f"{fname}()'s format")
        vals = [self.expr(a) for a in node.args[1:]]
        out, items, inputs, k = [], [], {}, 0
        pos = 0
        for m in _FMT_RE.finditer(fmt):
            out.append(fmt[pos:m.start()].replace("{", "{{").replace("}", "}}"))
            pos = m.end()
            conv = m.group("conv")
            if conv == "%":
                out.append("%")
                continue
            if conv == "c":
                raise FormulaError(f"{fname}(): %c isn't supported")
            if k >= len(vals):
                raise FormulaError(f"{fname}(): the format has more % placeholders than values")
            v = vals[k]
            k += 1
            spec = self._fmt_spec(m)
            if v.t == VECTOR or v.t.startswith("LIST:"):
                if v.t.startswith("LIST:"):
                    raise FormulaError(f"{fname}(): arrays can't be formatted — format their items")
                comps = [self.sep(v, i) for i in range(3)]
                fields = []
                for c in comps:
                    key = f"a{len(items)}"
                    items.append(("FLOAT", key))
                    inputs[f"Item_{len(items) - 1}"] = self.inp(c, FLOAT)
                    fields.append("{" + key + (":" + spec if spec else ":g") + "}")
                    if c.field and not allow_fields:
                        self._field_message(fname)
                out.append("{{" + ", ".join(fields) + "}}")
                continue
            if conv in "diuxX":
                t, itype = INT, "INT"
            elif conv in "fFgGeE":
                t, itype = FLOAT, "FLOAT"
            elif v.t == STRING:
                t, itype = STRING, "STRING"
            elif v.t in (INT, BOOL):
                t, itype = INT, "INT"
            else:
                t, itype = FLOAT, "FLOAT"
            if v.t == STRING and t != STRING:
                raise FormulaError(f"{fname}(): %{conv} needs a number, got text — use %s")
            cv = self.coerce(v, t, f"{fname}()'s value")
            if cv.field and not allow_fields:
                self._field_message(fname)
            key = f"a{len(items)}"
            items.append((itype, key))
            inputs[f"Item_{len(items) - 1}"] = self.inp(cv, t)
            out.append("{" + key + (":" + spec if spec else "") + "}")
        out.append(fmt[pos:].replace("{", "{{").replace("}", "}}"))
        if k < len(vals):
            raise FormulaError(f"{fname}(): {len(vals) - k} value(s) have no % placeholder in the format")
        text = "".join(out)
        if not items:
            return Val(STRING, c=text.replace("{{", "{").replace("}}", "}"))
        n = self.g.add("FunctionNodeFormatString", {}, {"Format": text, **inputs}, items=items,
                       items_attr="format_items")
        return Val(STRING, o=n.out("String"), field=any(v.field for v in vals))

    @staticmethod
    def _fmt_spec(m):
        """printf flags/width/precision → a Blender (Python-style) format spec."""
        flags, width, prec, conv = m.group("flags") or "", m.group("width"), m.group("prec"), m.group("conv")
        spec = ""
        if "-" in flags:
            spec += "<"
        if "+" in flags:
            spec += "+"
        elif " " in flags:
            spec += " "
        if "0" in flags and "-" not in flags:
            spec += "0"
        if width:
            spec += width
        if prec is not None:
            spec += "." + prec
        elif conv in "fF":
            spec += ".6"
        if conv in "fFeEgGxX":
            spec += conv
        elif conv in "diu" and spec:
            spec += "d"
        return spec

    def _field_message(self, fname):
        raise FormulaError(f"{fname}() can only show single values — summarize per-element values first, "
                           f"e.g. {fname}(\"%g\", avgof(@P.z)), or store them in an attribute and look at the "
                           f"spreadsheet")

    def f_sprintf(self, node, name):
        v = self.format_text(node, name, allow_fields=True)
        if v.field:
            self.require("string_fields")
        return v

    def f_message(self, node, name):
        level = FUNCS[name].data["level"]
        text = self.format_text(node, name, allow_fields=False)
        if text.is_const:
            text = Val(STRING, c=text.c.rstrip("\n"))
        show = self.selection()
        if show is None:
            show = Val(BOOL, c=True)
        elif show.field:
            # inside a per-element if: show it when any element takes this branch
            st = self.g.add("GeometryNodeAttributeStatistic", {"data_type": "FLOAT", "domain": self.field_domain()},
                            {"Geometry": self.ctx.geo, "Attribute": self.inp(self.coerce(show, FLOAT), FLOAT)})
            show = self.compare("GREATER_THAN", Val(FLOAT, o=st.out("Max")), Val(FLOAT, c=0.5))
        w = self.g.add("GeometryNodeWarning", {"warning_type": level},
                       {"Show": self.inp(show, BOOL), "Message": self.inp(text, STRING)}, pure=False,
                       label=self.cur_text[:60] or None)
        # thread it into the geometry chain so it runs inside zones too
        sw = self.g.add("GeometryNodeSwitch", {"input_type": "GEOMETRY"},
                        {"Switch": w.out("Show"), "False": self.ctx.geo, "True": self.ctx.geo}, pure=False)
        self.ctx.geo = sw.out("Output")
        return Val(VOID)
