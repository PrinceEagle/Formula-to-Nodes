# SPDX-License-Identifier: GPL-3.0-or-later
"""Front-end for the Formula to Nodes language.

Turns source text into a tree of statements whose expressions are already
parsed into Python ``ast`` nodes. Pure Python on purpose: no ``bpy`` import,
so the exact same parser runs inside Blender, in worker threads (AI
validation), in the MCP server and in plain unit tests.

Pipeline
    source ──scan()──► flat tokens (stmt / open-block / close-block / directive)
           ──_Parser──► nested Stmt objects, user functions, #runover, #define
           ──parse_expr()──► VEX-ish expression text → Python AST
"""

import ast
import re

__all__ = [
    "FormulaError", "Program", "parse_source", "parse_program", "parse_expr",
    "SAssign", "SDecl", "SReturn", "SExpr", "SBlock", "SFor", "SForeachArr", "SFunc",
    "walk", "RUNOVER_DOMAINS", "TYPE_WORDS", "list_type", "is_list", "elem_type",
]


class FormulaError(Exception):
    """A user-facing compile error, optionally tied to a 1-based line.

    ``source`` names the #include file the line belongs to (None = the script).
    """

    def __init__(self, message, line=None, source=None):
        super().__init__(message)
        self.message = message
        self.line = line
        self.source = source

    def __str__(self):
        if self.source:
            return f"{self.source}, line {self.line}: {self.message}" if self.line else \
                f"{self.source}: {self.message}"
        return f"Line {self.line}: {self.message}" if self.line else self.message


# ─────────────────────────────────────────────────────────────────────────────
#  Types shared with the compiler
# ─────────────────────────────────────────────────────────────────────────────

TYPE_WORDS = {"float": "FLOAT", "vector": "VECTOR", "vector3": "VECTOR", "vector4": "ROTATION",
              "int": "INT", "bool": "BOOL", "string": "STRING",
              "matrix": "MATRIX", "matrix4": "MATRIX", "matrix3": "MATRIX", "void": "VOID"}
_TYPES_RE = r"float|vector4|vector3|vector|int|bool|string|matrix4|matrix3|matrix"


def list_type(t):
    return "LIST:" + t


def is_list(t):
    return isinstance(t, str) and t.startswith("LIST:")


def elem_type(t):
    return t[5:] if is_list(t) else t


# Run-over / foreach domains. Houdini naming: "vertex" is a face corner,
# "prim" is a face (or a curve when running over curves).
RUNOVER_DOMAINS = {
    "point": "POINT", "points": "POINT",
    "vertex": "CORNER", "vertices": "CORNER", "corner": "CORNER", "corners": "CORNER",
    "prim": "FACE", "prims": "FACE", "primitive": "FACE", "primitives": "FACE",
    "face": "FACE", "faces": "FACE", "poly": "FACE", "polygon": "FACE", "polygons": "FACE",
    "edge": "EDGE", "edges": "EDGE",
    "curve": "CURVE", "curves": "CURVE", "spline": "CURVE", "splines": "CURVE",
    "instance": "INSTANCE", "instances": "INSTANCE",
    "layer": "LAYER", "layers": "LAYER",
    "detail": "DETAIL", "numbers": "DETAIL", "once": "DETAIL",
}
FOREACH_DOMAINS = {k: v for k, v in RUNOVER_DOMAINS.items() if v not in ("DETAIL", "LAYER")}


# ─────────────────────────────────────────────────────────────────────────────
#  Statement objects
# ─────────────────────────────────────────────────────────────────────────────

class Stmt:
    __slots__ = ("line", "sid", "src")

    def __init__(self, line):
        self.line = line
        self.sid = -1
        self.src = None          # include name, None for the main script

    def exprs(self):
        """Expression ASTs this statement evaluates (for read analysis)."""
        return []


class SAssign(Stmt):
    """target (op)= value. ``op`` is None for plain '=' or an ast operator."""
    __slots__ = ("target", "op", "value", "text")

    def __init__(self, line, target, op, value, text):
        super().__init__(line)
        self.target, self.op, self.value, self.text = target, op, value, text

    def exprs(self):
        return [self.value, self.target]


class SDecl(Stmt):
    __slots__ = ("vtype", "name", "value", "text")

    def __init__(self, line, vtype, name, value, text):
        super().__init__(line)
        self.vtype, self.name, self.value, self.text = vtype, name, value, text

    def exprs(self):
        return [self.value] if self.value is not None else []


class SReturn(Stmt):
    __slots__ = ("value", "text")

    def __init__(self, line, value, text):
        super().__init__(line)
        self.value, self.text = value, text

    def exprs(self):
        return [self.value] if self.value is not None else []


class SExpr(Stmt):
    __slots__ = ("value", "text")

    def __init__(self, line, value, text):
        super().__init__(line)
        self.value, self.text = value, text

    def exprs(self):
        return [self.value]


class SBlock(Stmt):
    """kind: 'sim' | 'foreach' | 'repeat' | 'if' | 'runover' | 'init'
    (subclasses: 'for', 'forarr').

    arg is the domain string for foreach/runover, the parsed expression for
    repeat and if, None for sim/init. ``orelse`` is only used by 'if'.
    """
    __slots__ = ("kind", "arg", "body", "orelse", "end_sid", "text")

    def __init__(self, line, kind, arg, body, orelse=None, text=""):
        super().__init__(line)
        self.kind, self.arg, self.body, self.orelse = kind, arg, body, orelse
        self.end_sid = -1
        self.text = text

    def exprs(self):
        return [self.arg] if isinstance(self.arg, ast.AST) else []


class SFor(SBlock):
    """for (init; cond; step) { body }"""
    __slots__ = ("init", "cond", "step")

    def __init__(self, line, init, cond, step, body, text=""):
        super().__init__(line, "for", None, body, text=text)
        self.init, self.cond, self.step = init, cond, step

    def exprs(self):
        out = []
        if self.init is not None:
            out += self.init.exprs()
        if self.cond is not None:
            out.append(self.cond)
        if self.step is not None:
            out += self.step.exprs()
        return out


class SForeachArr(SBlock):
    """foreach (type name; array) { body }"""
    __slots__ = ("vtype", "name")

    def __init__(self, line, vtype, name, arr, body, text=""):
        super().__init__(line, "forarr", arr, body, text=text)
        self.vtype, self.name = vtype, name


class SFunc:
    """A user function: rtype name(params) { body }. Not a statement of the
    program — call sites inline it."""
    __slots__ = ("line", "rtype", "name", "params", "body", "text", "src")

    def __init__(self, line, rtype, name, params, body, text, src=None):
        self.line, self.rtype, self.name, self.params = line, rtype, name, params
        self.body, self.text, self.src = body, text, src


class Program:
    """Parsed script: top-level statements plus everything the directives set."""

    def __init__(self):
        self.stmts = []
        self.functions = {}        # name → [SFunc] (overloads by argument count)
        self.runover = None        # domain id from #runover, or None
        self.runover_line = None
        self.defines = {}
        self.includes = []         # names in the order they were included
        self.pragmas = []


def walk(stmts):
    """Depth-first iteration over every statement, blocks included."""
    for s in stmts:
        yield s
        if isinstance(s, SBlock):
            yield from walk(s.body)
            if s.orelse:
                yield from walk(s.orelse)


# ─────────────────────────────────────────────────────────────────────────────
#  Scanner
# ─────────────────────────────────────────────────────────────────────────────

# chars that, ending a line, mean the statement obviously continues
_CONTINUE_END = set("+-*/%^=,?:&|<>!(")
# chars that, starting the next line, mean it continues the previous one
_CONTINUE_START = set("+-*/%^?:&|<>=.")

_DIRECTIVE_RE = re.compile(r"#(include|runover|define|pragma)\b")
_FUNC_RE = re.compile(
    r"(?:function\s+)?(?:export\s+)?(?P<rtype>float|vector4|vector3|vector|int|bool|string|"
    r"matrix4|matrix3|matrix|void)\s*(?P<arr>\[\s*\])?\s+(?P<name>[A-Za-z_]\w*)\s*\((?P<params>.*)\)",
    re.S)


def _skip_string(src, i, line):
    quote = src[i]
    j = i + 1
    n = len(src)
    while j < n and src[j] != quote:
        if src[j] == "\\":
            j += 1
        elif src[j] == "\n":
            raise FormulaError("unterminated string literal", line)
        j += 1
    if j >= n:
        raise FormulaError("unterminated string literal", line)
    return j + 1


def _match_paren(text, open_idx):
    """Index of the ')' matching text[open_idx] == '(' or -1."""
    depth = 0
    i, n = open_idx, len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            q = c
            i += 1
            while i < n and text[i] != q:
                i += 2 if text[i] == "\\" else 1
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _keyword_paren(text, keyword):
    """If text is ``keyword (inner) rest`` returns (inner, rest) else None."""
    m = re.match(rf"{keyword}\s*\(", text)
    if not m:
        return None
    open_idx = m.end() - 1
    close = _match_paren(text, open_idx)
    if close < 0:
        return None
    return text[open_idx + 1:close], text[close + 1:].strip()


def _classify_header(text):
    """(kind, payload) for a block header like 'if (x > 0)', or None."""
    t = text.strip()
    if re.fullmatch(r"(?:simulate|sim)", t):
        return ("sim", None)
    if t == "init":
        return ("init", None)
    if t == "foreach":
        return ("foreach", "point")
    kp = _keyword_paren(t, "foreach")
    if kp is not None and kp[1] == "":
        if ";" in kp[0]:
            return ("forarr", kp[0])
        return ("foreach", kp[0].strip() or "point")
    for kw in ("repeat", "if", "for", "runover", "while"):
        kp = _keyword_paren(t, kw)
        if kp is not None and kp[1] == "":
            return (kw, kp[0])
    if t == "do":
        return ("do", None)
    if t.startswith("else"):
        rest = t[4:].strip()
        if rest == "":
            return ("else", None)
        kp = _keyword_paren(rest, "if")
        if kp is not None and kp[1] == "" and rest.startswith("if"):
            return ("elseif", kp[0])
    m = _FUNC_RE.fullmatch(t)
    if m:
        return ("func", m)
    return None


def _peek_code_char(src, i):
    """Next char that isn't whitespace or inside a comment, or ''."""
    n = len(src)
    while i < n:
        c = src[i]
        if c.isspace():
            i += 1
        elif src.startswith("//", i) or c == "#":
            j = src.find("\n", i)
            if j < 0:
                return ""
            i = j + 1
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j < 0:
                return ""
            i = j + 2
        else:
            return c
    return ""


def scan(src):
    """Split source into ('stmt', text, line) / ('open', header, line) /
    ('close', '}', line) / ('directive', text, line) tokens. Handles strings,
    // # /* */ comments, ';' and newline terminators, vector-literal braces
    vs block braces, and obvious multi-line continuations."""
    tokens = []
    buf = []
    buf_line = None
    depth = 0
    line = 1
    i, n = 0, len(src)

    def flush():
        nonlocal buf, buf_line
        text = "".join(buf).strip()
        if text:
            tokens.append(("stmt", text, buf_line or line))
        buf = []
        buf_line = None

    while i < n:
        c = src[i]
        if c in "\"'":
            j = _skip_string(src, i, line)
            if buf_line is None:
                buf_line = line
            buf.append(src[i:j])
            i = j
            continue
        if c == "#" and depth == 0 and not "".join(buf).strip() and _DIRECTIVE_RE.match(src, i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            tokens.append(("directive", src[i:j].strip(), line))
            i = j
            continue
        if src.startswith("//", i) or c == "#":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j < 0:
                raise FormulaError("unterminated /* comment", line)
            line += src.count("\n", i, j)
            buf.append(" ")
            i = j + 2
            continue
        if c == "\n":
            if depth == 0:
                text = "".join(buf).strip()
                if text:
                    nxt = _peek_code_char(src, i + 1)
                    ends_incdec = text.endswith(("++", "--"))
                    continues = (
                        (text[-1] in _CONTINUE_END and not ends_incdec)
                        or (nxt and nxt in _CONTINUE_START and not ends_incdec)
                        or _classify_header(text) is not None
                    )
                    if not continues:
                        flush()
                    else:
                        buf.append(" ")
            else:
                buf.append(" ")
            line += 1
            i += 1
            continue
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
            if depth < 0:
                raise FormulaError(f"unmatched '{c}'", line)
        elif c == "{":
            text = "".join(buf).strip()
            if depth == 0 and _classify_header(text) is not None:
                tokens.append(("open", text, buf_line or line))
                buf = []
                buf_line = None
                i += 1
                continue
            depth += 1
        elif c == "}":
            if depth == 0:
                flush()
                tokens.append(("close", "}", line))
                i += 1
                continue
            depth -= 1
        elif c == ";" and depth == 0:
            flush()
            i += 1
            continue
        if buf_line is None and not c.isspace():
            buf_line = line
        buf.append(c)
        i += 1

    if depth > 0:
        raise FormulaError("unclosed '(' or '{' — check your brackets", buf_line or line)
    flush()
    return tokens


# ─────────────────────────────────────────────────────────────────────────────
#  Expression preprocessing: VEX-flavoured text → Python source
# ─────────────────────────────────────────────────────────────────────────────

ATTR_PREFIX = "_attr_"
_ATTR_RE = re.compile(r"(?<![\w@])([fivbspcu34]?)@([A-Za-z_]\w*)")
_STR_RE = re.compile(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'")
_IDENT_RE = re.compile(r"(?<![\w@])[A-Za-z_]\w*")


def attr_placeholder(prefix, name):
    return f"{ATTR_PREFIX}{prefix or 'n'}_{name}"


def split_attr_placeholder(ident):
    """'_attr_f_moss' → ('f', 'moss'); non-attributes → None."""
    if not ident.startswith(ATTR_PREFIX):
        return None
    rest = ident[len(ATTR_PREFIX):]
    if len(rest) < 3 or rest[1] != "_":
        return None
    prefix = "" if rest[0] == "n" else rest[0]
    return prefix, rest[2:]


def _protect_strings(text):
    saved = []

    def repl(m):
        saved.append(m.group(0))
        return f"__STR{len(saved) - 1}__"
    return _STR_RE.sub(repl, text), saved


def _restore_strings(text, saved):
    for idx, s in enumerate(saved):
        text = text.replace(f"__STR{idx}__", s)
    return text


def _convert_ternary(s):
    """Rewrite C-style ``a ? b : c`` into Python ``((b) if (a) else (c))``
    everywhere, including inside brackets and function arguments. A ':' that
    belongs to a slice (``s[1:3]``) is left alone."""
    # 1. recurse into every bracket group, splitting on top-level commas
    out = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in "([{":
            close_char = {"(": ")", "[": "]", "{": "}"}[c]
            depth, j = 0, i
            while j < n:
                if s[j] in "([{":
                    depth += 1
                elif s[j] in ")]}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            inner = s[i + 1:j]
            if c == "[" and "?" not in inner:
                out.append(c + inner + (s[j] if j < n else close_char))   # slices keep their ':'
            else:
                parts = _split_top(inner, ",")
                out.append(c + ",".join(_convert_ternary(p) for p in parts) + (s[j] if j < n else close_char))
            i = j + 1
            continue
        out.append(c)
        i += 1
    s = "".join(out)

    # 2. top-level '?'
    q = _find_top(s, "?")
    if q < 0:
        if _find_top(s, ":") >= 0:
            raise FormulaError("':' without a matching '?'")
        return s
    nest, colon, depth = 0, -1, 0
    for j in range(q + 1, len(s)):
        ch = s[j]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0 and ch == "?":
            nest += 1
        elif depth == 0 and ch == ":":
            if nest == 0:
                colon = j
                break
            nest -= 1
    if colon < 0:
        raise FormulaError("'?' without a matching ':' in a ternary expression")
    cond, a, b = s[:q], s[q + 1:colon], s[colon + 1:]
    if not cond.strip() or not a.strip() or not b.strip():
        raise FormulaError("incomplete ternary — use condition ? value_if_true : value_if_false")
    return f"(({_convert_ternary(a)}) if ({cond}) else ({_convert_ternary(b)}))"


def _split_top(s, sep):
    parts, depth, start = [], 0, 0
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in "\"'":
            q = c
            i += 1
            while i < n and s[i] != q:
                i += 2 if s[i] == "\\" else 1
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == sep and depth == 0:
            parts.append(s[start:i])
            start = i + 1
        i += 1
    parts.append(s[start:])
    return parts


def _find_top(s, ch):
    depth = 0
    for i, c in enumerate(s):
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == ch and depth == 0:
            return i
    return -1


def preprocess(text):
    """VEX-flavoured expression text → Python expression source."""
    code, saved = _protect_strings(text.strip())
    if re.search(r"(?<![\w@])[fivbspcu34]\[\]@", code):
        raise FormulaError("array attributes (f[]@name) aren't possible in Blender — attributes hold one value "
                           "per element; use an array variable (float a[] = ...) or several attributes")
    code = code.replace("&&", " and ").replace("||", " or ")
    code = re.sub(r"!(?!=)", " not ", code)
    code = code.replace("^", "**")
    code = _ATTR_RE.sub(lambda m: attr_placeholder(m.group(1), m.group(2)), code)
    if "@" in code:
        bad = re.search(r"\S*@\S*", code).group(0)
        raise FormulaError(
            f"can't read '{bad}' — attributes look like @name, f@name, v@name, i@name, "
            f"b@name, p@name (rotation) or 4@name (matrix)")
    if "?" in code or ":" in code:
        code = _convert_ternary(code)
    return _restore_strings(code.strip(), saved)


def parse_expr(text, line=None):
    """Parse one expression (VEX-flavoured) into a Python AST node."""
    if text is None or not text.strip():
        raise FormulaError("expected an expression", line)
    try:
        py = preprocess(text)
    except FormulaError as e:
        raise FormulaError(e.message, line) from None
    try:
        tree = ast.parse(py, mode="eval")
    except SyntaxError:
        hint = ""
        if re.search(r"\b(while|do)\b", text):
            hint = " — while/do loops aren't supported; use for (int i = 0; i < n; i++) { } or repeat(n) { }"
        elif re.search(r"\b(break|continue)\b", text):
            hint = " — break/continue aren't supported; guard the rest of the loop with if (...)"
        elif "{" in text:
            hint = " — vector literals need 3 values {x, y, z} (4 for a rotation {x, y, z, w})"
        raise FormulaError(f"syntax error in '{text.strip()}'{hint}", line) from None
    return tree.body


# ─────────────────────────────────────────────────────────────────────────────
#  Simple statements
# ─────────────────────────────────────────────────────────────────────────────

_AUG_OPS = {
    "+": ast.Add, "-": ast.Sub, "*": ast.Mult, "/": ast.Div,
    "%": ast.Mod, "^": ast.Pow, "**": ast.Pow,
}

_DECL_RE = re.compile(
    rf"(?:const\s+)?({_TYPES_RE})\s*(\[\s*\])?\s+([A-Za-z_]\w*)\s*(\[\s*\])?\s*(?:=\s*(.*))?", re.S)
_MULTI_DECL_RE = re.compile(rf"(?:const\s+)?({_TYPES_RE})\s*(\[\s*\])?\s+(?=[A-Za-z_])(.*)", re.S)


def _find_assign_op(text):
    """(start, end, op_symbol) of the top-level assignment operator, or None."""
    depth = 0
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            q = c
            i += 1
            while i < n and text[i] != q:
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "=" and depth == 0:
            if i + 1 < n and text[i + 1] == "=":
                i += 2
                continue
            prev = text[i - 1] if i > 0 else ""
            if prev in "!<>=":
                i += 1
                continue
            if text[max(0, i - 2):i] == "**":
                return i - 2, i + 1, "**"
            if prev in "+-*/%^":
                return i - 1, i + 1, prev
            return i, i + 1, ""
        i += 1
    return None


def _decl(line, m, text):
    word, arr1, name, arr2, value = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
    vtype = TYPE_WORDS[word]
    if arr1 or arr2:
        vtype = list_type(vtype)
    if value is not None and not value.strip():
        raise FormulaError(f"'{name}' has '=' but no value", line)
    if value is not None and re.match(r"\s*=", value):
        raise FormulaError("use '=' to declare a variable, not '=='", line)
    parsed = parse_expr(value, line) if value is not None else None
    return SDecl(line, vtype, name, parsed, text)


def parse_simple(text, line):
    """One statement → a Stmt, or a list of SDecl for ``float a = 1, b = 2``."""
    t = text.strip()

    m = re.fullmatch(r"return\b\s*(.*)", t, re.S)
    if m:
        value = m.group(1).strip()
        return SReturn(line, parse_expr(value, line) if value else None, t)

    m = _DECL_RE.fullmatch(t)
    if m and "," not in _top_level_commas(t):
        return _decl(line, m, t)
    m2 = _MULTI_DECL_RE.fullmatch(t)
    if m2 and _top_level_commas(m2.group(3)):
        word, arr = m2.group(1), m2.group(2) or ""
        out = []
        for part in _split_top(m2.group(3), ","):
            part = part.strip()
            if not part:
                raise FormulaError("empty declaration between commas", line)
            mm = _DECL_RE.fullmatch(f"{word}{arr} {part}")
            if not mm:
                raise FormulaError(f"can't read the declaration '{part}'", line)
            out.append(_decl(line, mm, f"{word}{arr} {part}"))
        return out
    if m:
        return _decl(line, m, t)

    m = re.fullmatch(r"(\+\+|--)\s*(.+)", t, re.S)
    if m and _find_assign_op(t) is None:
        target = _parse_target(m.group(2), line)
        op = ast.Add if m.group(1) == "++" else ast.Sub
        return SAssign(line, target, op, ast.Constant(1), t)

    m = re.fullmatch(r"(.+?)\s*(\+\+|--)", t, re.S)
    if m and _find_assign_op(t) is None:
        target = _parse_target(m.group(1), line)
        op = ast.Add if m.group(2) == "++" else ast.Sub
        return SAssign(line, target, op, ast.Constant(1), t)

    found = _find_assign_op(t)
    if found:
        start, end, sym = found
        lhs, rhs = t[:start].strip(), t[end:].strip()
        if not lhs:
            raise FormulaError("assignment is missing its left-hand side", line)
        if not rhs:
            raise FormulaError(f"nothing to assign to '{lhs}'", line)
        target = _parse_target(lhs, line)
        op = _AUG_OPS[sym] if sym else None
        return SAssign(line, target, op, parse_expr(rhs, line), t)

    if re.match(r"(?:break|continue)\b", t):
        raise FormulaError(f"'{t}' isn't supported — guard the rest of the loop body with if (...) instead", line)
    return SExpr(line, parse_expr(t, line), t)


def _top_level_commas(text):
    """',' if text has a comma outside brackets/strings, else ''."""
    return "," if len(_split_top(text, ",")) > 1 else ""


def _parse_target(lhs, line):
    node = parse_expr(lhs, line)
    ok = isinstance(node, ast.Name) or (
        isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
    ) or (
        isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
    )
    if not ok:
        raise FormulaError(
            f"can't assign to '{lhs}' — the left side must be an attribute "
            f"(f@name, @position.z) or a variable name", line)
    return node


def _as_list(x):
    return x if isinstance(x, list) else [x]


# ─────────────────────────────────────────────────────────────────────────────
#  Parser: tokens → statement tree
# ─────────────────────────────────────────────────────────────────────────────

class _Parser:
    MAX_INCLUDE_DEPTH = 8

    def __init__(self, resolver=None):
        self.resolver = resolver
        self.prog = Program()
        self.src_name = None         # include currently being parsed
        self.include_stack = []

    # ── defines ───────────────────────────────────────────────────────────
    def _expand(self, text):
        if not self.prog.defines:
            return text
        code, saved = _protect_strings(text)
        for _ in range(8):
            new = _IDENT_RE.sub(lambda m: self.prog.defines.get(m.group(0), m.group(0)), code)
            if new == code:
                break
            code = new
        return _restore_strings(code, saved)

    def _err(self, msg, line):
        return FormulaError(msg, line, self.src_name)

    # ── entry ─────────────────────────────────────────────────────────────
    def parse(self, src):
        src = src.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
        try:
            tokens = scan(src)
        except FormulaError as e:
            raise self._err(e.message, e.line) from None
        body, _ = self._block(tokens, 0, closing=False, open_line=None, top=True)
        if self.src_name is not None:
            for s in walk(body):
                s.src = self.src_name
        return body

    # ── blocks ────────────────────────────────────────────────────────────
    def _block(self, tokens, i, closing, open_line, top=False):
        body = []
        n = len(tokens)
        while i < n:
            kind, text, line = tokens[i]
            if kind == "close":
                if closing:
                    return body, i + 1
                raise self._err("stray '}' — there is no open block to close", line)
            if kind == "directive":
                if not top:
                    raise self._err("#directives must be at the top level, not inside a block", line)
                body += self._directive(text, line)
                i += 1
                continue
            if kind == "open":
                hk, payload = _classify_header(self._expand(text)) or _classify_header(text)
                if hk in ("else", "elseif"):
                    raise self._err("'else' without a matching 'if'", line)
                if hk in ("while", "do"):
                    raise self._err("while/do loops aren't supported — use for (int i = 0; i < n; i++) { } "
                                    "or repeat(n) { }", line)
                if hk == "func":
                    if not top:
                        raise self._err("functions must be defined at the top level", line)
                    inner, i = self._block(tokens, i + 1, closing=True, open_line=line)
                    self._add_function(payload, inner, line, text)
                    continue
                inner, i = self._block(tokens, i + 1, closing=True, open_line=line)
                block = self._make_block(hk, payload, inner, line, text)
                if hk == "if":
                    i = self._else_chain(block, tokens, i)
                body.append(block)
                continue
            stmts, i = self._inline(tokens, i)
            body += stmts
        if closing:
            raise self._err("block opened here is never closed — add a '}'", open_line)
        return body, i

    def _inline(self, tokens, i):
        kind, text, line = tokens[i]
        text = self._expand(text)
        if re.match(r"if\s*\(", text):
            kp = _keyword_paren(text, "if")
            if kp is None:
                raise self._err("malformed if — expected if (condition) { ... }", line)
            cond, rest = kp
            if not rest:
                raise self._err("if (...) needs a body: a statement or { ... }", line)
            inner, _ = self._block([("stmt", rest, line)], 0, closing=False, open_line=line)
            block = self._make_block("if", cond, inner, line, f"if ({cond})")
            return [block], self._else_chain(block, tokens, i + 1)
        if re.match(r"for\s*\(", text):
            kp = _keyword_paren(text, "for")
            if kp is None or not kp[1]:
                raise self._err("malformed for — expected for (init; condition; step) { ... }", line)
            inner, _ = self._block([("stmt", kp[1], line)], 0, closing=False, open_line=line)
            return [self._make_block("for", kp[0], inner, line, f"for ({kp[0]})")], i + 1
        if re.match(r"foreach\s*\(", text):
            kp = _keyword_paren(text, "foreach")
            if kp is None or not kp[1]:
                raise self._err("malformed foreach — expected foreach (float x; values) { ... }", line)
            kind = "forarr" if ";" in kp[0] else "foreach"
            inner, _ = self._block([("stmt", kp[1], line)], 0, closing=False, open_line=line)
            return [self._make_block(kind, kp[0], inner, line, f"foreach ({kp[0]})")], i + 1
        if re.match(r"repeat\s*\(", text):
            kp = _keyword_paren(text, "repeat")
            if kp is not None and kp[1]:
                inner, _ = self._block([("stmt", kp[1], line)], 0, closing=False, open_line=line)
                return [self._make_block("repeat", kp[0], inner, line, f"repeat ({kp[0]})")], i + 1
        if re.match(r"(while|do)\b", text):
            raise self._err("while/do loops aren't supported — use for (int i = 0; i < n; i++) { } "
                            "or repeat(n) { }", line)
        if re.match(r"else\b", text):
            raise self._err("'else' without a matching 'if'", line)
        try:
            return _as_list(parse_simple(text, line)), i + 1
        except FormulaError as e:
            raise self._err(e.message, e.line) from None

    def _else_chain(self, block, tokens, i):
        if i >= len(tokens):
            return i
        kind, text, line = tokens[i]
        if kind == "open":
            hk, payload = _classify_header(self._expand(text)) or _classify_header(text)
            if hk == "else":
                inner, i = self._block(tokens, i + 1, closing=True, open_line=line)
                block.orelse = inner
                return i
            if hk == "elseif":
                inner, i = self._block(tokens, i + 1, closing=True, open_line=line)
                nested = self._make_block("if", payload, inner, line, f"else if ({payload})")
                block.orelse = [nested]
                return self._else_chain(nested, tokens, i)
            return i
        if kind == "stmt" and re.match(r"else\b", text):
            rest = text[4:].strip()
            if not rest:
                raise self._err("'else' needs a body: a statement or { ... }", line)
            inner, _ = self._block([("stmt", rest, line)], 0, closing=False, open_line=line)
            block.orelse = inner
            # an inline 'else if (...) stmt' produced a nested if; its own else
            # can only come from the following tokens
            if len(inner) == 1 and isinstance(inner[0], SBlock) and inner[0].kind == "if" \
                    and rest.startswith("if"):
                return self._else_chain(inner[0], tokens, i + 1)
            return i + 1
        return i

    def _make_block(self, kind, payload, body, line, text):
        try:
            return self._make_block_inner(kind, payload, body, line, text)
        except FormulaError as e:
            if e.source is None:
                raise self._err(e.message, e.line or line) from None
            raise

    def _make_block_inner(self, kind, payload, body, line, text):
        if kind == "sim":
            return SBlock(line, "sim", None, body, text=text)
        if kind == "init":
            return SBlock(line, "init", None, body, text=text)
        if kind == "foreach":
            word = payload.strip().lower()
            if word not in FOREACH_DOMAINS:
                raise FormulaError(
                    f"unknown foreach domain '{payload}' — use point, edge, face, vertex, curve or instance",
                    line)
            return SBlock(line, "foreach", FOREACH_DOMAINS[word], body, text=text)
        if kind == "runover":
            word = payload.strip().lower()
            if word not in RUNOVER_DOMAINS:
                raise FormulaError(f"unknown run-over domain '{payload}' — use point, vertex, prim, edge, "
                                   f"curve, instance or detail", line)
            return SBlock(line, "runover", RUNOVER_DOMAINS[word], body, text=text)
        if kind == "repeat":
            if not payload.strip():
                raise FormulaError("repeat needs a count, e.g. repeat(5) { ... }", line)
            return SBlock(line, "repeat", parse_expr(payload, line), body, text=text)
        if kind == "if":
            if not payload.strip():
                raise FormulaError("if needs a condition, e.g. if (v@position.z > 0) { ... }", line)
            return SBlock(line, "if", parse_expr(payload, line), body, text=text)
        if kind == "for":
            parts = _split_top(payload, ";")
            if len(parts) != 3:
                raise FormulaError("for needs three parts: for (int i = 0; i < 10; i++)", line)
            init_t, cond_t, step_t = (p.strip() for p in parts)
            init = parse_simple(init_t, line) if init_t else None
            if isinstance(init, list):
                raise FormulaError("declare one loop variable: for (int i = 0; i < n; i++)", line)
            if init is not None and not isinstance(init, (SDecl, SAssign)):
                raise FormulaError("the first part of for (...) must declare or set the loop variable", line)
            if not cond_t:
                raise FormulaError("for (...) needs a condition — loops must end", line)
            cond = parse_expr(cond_t, line)
            step = parse_simple(step_t, line) if step_t else None
            if step is not None and not isinstance(step, SAssign):
                raise FormulaError("the last part of for (...) must change the loop variable, e.g. i++", line)
            return SFor(line, init, cond, step, body, text=text)
        if kind == "forarr":
            m = re.fullmatch(rf"\s*(?:const\s+)?({_TYPES_RE})\s+([A-Za-z_]\w*)\s*;\s*(.+)", payload, re.S)
            if not m:
                raise FormulaError("foreach over an array looks like: foreach (float x; values) { ... }", line)
            return SForeachArr(line, TYPE_WORDS[m.group(1)], m.group(2), parse_expr(m.group(3), line),
                               body, text=text)
        raise FormulaError(f"unexpected block '{text}'", line)

    # ── functions ─────────────────────────────────────────────────────────
    def _add_function(self, m, body, line, text):
        rtype = TYPE_WORDS[m.group("rtype")]
        if m.group("arr"):
            rtype = list_type(rtype)
        name = m.group("name")
        params = self._params(m.group("params"), line)
        _number(body, [0])
        fn = SFunc(line, rtype, name, params, body, text.strip(), self.src_name)
        if self.src_name is not None:
            for s in walk(body):
                s.src = self.src_name
        overloads = self.prog.functions.setdefault(name, [])
        if any(len(f.params) == len(params) for f in overloads):
            raise self._err(f"function '{name}' with {len(params)} argument(s) is defined twice", line)
        overloads.append(fn)

    def _params(self, text, line):
        out = []
        if not text.strip():
            return out
        for group in _split_top(text, ";"):
            group = group.strip()
            if not group:
                continue
            cur_type = None
            for item in _split_top(group, ","):
                item = re.sub(r"^\s*(?:const|export)\s+", "", item.strip())
                m = re.fullmatch(rf"({_TYPES_RE})\s*(\[\s*\])?\s+([A-Za-z_]\w*)\s*(\[\s*\])?", item)
                if m:
                    cur_type = TYPE_WORDS[m.group(1)]
                    t = list_type(cur_type) if (m.group(2) or m.group(4)) else cur_type
                    out.append((t, m.group(3)))
                    continue
                m = re.fullmatch(r"([A-Za-z_]\w*)\s*(\[\s*\])?", item)
                if m and cur_type is not None:
                    out.append((list_type(cur_type) if m.group(2) else cur_type, m.group(1)))
                    continue
                raise self._err(f"can't read the function parameter '{item}' — use e.g. (float a; vector b)", line)
        names = [p[1] for p in out]
        dup = {n for n in names if names.count(n) > 1}
        if dup:
            raise self._err(f"parameter '{sorted(dup)[0]}' appears twice", line)
        return out

    # ── directives ────────────────────────────────────────────────────────
    def _directive(self, text, line):
        m = _DIRECTIVE_RE.match(text)
        word = m.group(1)
        rest = text[m.end():].strip()
        if word == "runover":
            if self.src_name is not None:
                raise self._err("#runover can't be used inside an #include", line)
            dom = rest.split()[0].lower() if rest else ""
            if dom not in RUNOVER_DOMAINS:
                raise self._err("#runover needs a domain: point, vertex, prim, edge, curve, instance or detail",
                                line)
            if self.prog.runover is not None:
                raise self._err(f"#runover was already set on line {self.prog.runover_line}", line)
            self.prog.runover, self.prog.runover_line = RUNOVER_DOMAINS[dom], line
            return []
        if word == "define":
            mm = re.fullmatch(r"([A-Za-z_]\w*)(?:\s+(.*))?", rest, re.S)
            if not mm:
                raise self._err("#define needs a name and a value: #define SPEED 2.5", line)
            if "(" in mm.group(1):
                raise self._err("macros with arguments aren't supported — write a function instead", line)
            self.prog.defines[mm.group(1)] = f"({mm.group(2).strip()})" if mm.group(2) else "1"
            return []
        if word == "pragma":
            self.prog.pragmas.append(rest)
            return []
        # include
        mm = re.fullmatch(r"[\"<]([^\">]+)[\">]", rest)
        if not mm:
            raise self._err('#include needs a name in quotes: #include "falloff.h"', line)
        name = mm.group(1).strip()
        if self.resolver is None:
            raise self._err("#include isn't available here", line)
        if name in self.include_stack:
            raise self._err(f"'{name}' includes itself", line)
        if len(self.include_stack) >= self.MAX_INCLUDE_DEPTH:
            raise self._err("#include nesting is too deep", line)
        if name in self.prog.includes:
            return []            # include guards are implicit
        try:
            text_in = self.resolver(name)
        except FormulaError as e:
            raise self._err(e.message, line) from None
        if text_in is None:
            raise self._err(f"can't find the include '{name}' — check the name, your snippet folder, "
                            f"or a Text datablock with that name", line)
        self.prog.includes.append(name)
        saved = self.src_name
        self.include_stack.append(name)
        self.src_name = name
        try:
            body = self.parse(text_in)
        finally:
            self.src_name = saved
            self.include_stack.pop()
        return body


def _number(stmts, counter):
    for s in stmts:
        s.sid = counter[0]
        counter[0] += 1
        if isinstance(s, SBlock):
            _number(s.body, counter)
            if s.orelse:
                _number(s.orelse, counter)
            s.end_sid = counter[0] - 1


def parse_source(src, resolver=None):
    """Source text → Program. ``resolver(name)`` returns the text of an
    #include (or None if it doesn't exist)."""
    p = _Parser(resolver)
    body = p.parse(src)
    _number(body, [0])
    p.prog.stmts = body
    return p.prog


def parse_program(src):
    """Source text → list of Stmt (with nested SBlock bodies)."""
    return parse_source(src).stmts
