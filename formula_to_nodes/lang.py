# SPDX-License-Identifier: GPL-3.0-or-later
"""Front-end for the Formula to Nodes language.

Turns source text into a tree of statements whose expressions are already
parsed into Python ``ast`` nodes. Pure Python on purpose: no ``bpy`` import,
so the exact same parser runs inside Blender, in worker threads (AI
validation) and in plain unit tests.

Pipeline
    source ──scan()──► flat tokens (stmt / open-block / close-block)
           ──_build_tree()──► nested Stmt objects
           ──parse_expr()──► VEX-ish expression text → Python AST
"""

import ast
import re

__all__ = [
    "FormulaError", "parse_program", "parse_expr",
    "SAssign", "SDecl", "SReturn", "SExpr", "SBlock", "walk",
]


class FormulaError(Exception):
    """A user-facing compile error, optionally tied to a 1-based line."""

    def __init__(self, message, line=None):
        super().__init__(message)
        self.message = message
        self.line = line

    def __str__(self):
        return f"Line {self.line}: {self.message}" if self.line else self.message


# ─────────────────────────────────────────────────────────────────────────────
#  Statement objects
# ─────────────────────────────────────────────────────────────────────────────

class Stmt:
    __slots__ = ("line", "sid")

    def __init__(self, line):
        self.line = line
        self.sid = -1


class SAssign(Stmt):
    """target (op)= value. ``op`` is None for plain '=' or an ast operator."""
    __slots__ = ("target", "op", "value", "text")

    def __init__(self, line, target, op, value, text):
        super().__init__(line)
        self.target, self.op, self.value, self.text = target, op, value, text


class SDecl(Stmt):
    __slots__ = ("vtype", "name", "value", "text")

    def __init__(self, line, vtype, name, value, text):
        super().__init__(line)
        self.vtype, self.name, self.value, self.text = vtype, name, value, text


class SReturn(Stmt):
    __slots__ = ("value", "text")

    def __init__(self, line, value, text):
        super().__init__(line)
        self.value, self.text = value, text


class SExpr(Stmt):
    __slots__ = ("value", "text")

    def __init__(self, line, value, text):
        super().__init__(line)
        self.value, self.text = value, text


class SBlock(Stmt):
    """kind: 'sim' | 'foreach' | 'repeat' | 'if'.

    arg is the domain string for foreach, the parsed expression for repeat
    and if, None for sim. ``orelse`` is only used by 'if'.
    """
    __slots__ = ("kind", "arg", "body", "orelse", "end_sid", "text")

    def __init__(self, line, kind, arg, body, orelse=None, text=""):
        super().__init__(line)
        self.kind, self.arg, self.body, self.orelse = kind, arg, body, orelse
        self.end_sid = -1
        self.text = text


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

_TYPE_WORDS = {"float": "FLOAT", "vector": "VECTOR", "vector3": "VECTOR",
               "int": "INT", "bool": "BOOL"}

FOREACH_DOMAINS = {
    "point": "POINT", "points": "POINT", "vertex": "POINT", "vertices": "POINT",
    "edge": "EDGE", "edges": "EDGE",
    "face": "FACE", "faces": "FACE", "prim": "FACE", "primitive": "FACE",
    "curve": "CURVE", "curves": "CURVE", "spline": "CURVE",
    "instance": "INSTANCE", "instances": "INSTANCE",
}

# chars that, ending a line, mean the statement obviously continues
_CONTINUE_END = set("+-*/%^=,?:&|<>!(")
# chars that, starting the next line, mean it continues the previous one
_CONTINUE_START = set("+-*/%^?:&|<>=.")


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
    m = re.fullmatch(r"foreach\s*(?:\(\s*(\w*)\s*\))?", t)
    if m:
        return ("foreach", (m.group(1) or "point"))
    for kw, kind in (("repeat", "repeat"), ("if", "if")):
        kp = _keyword_paren(t, kw)
        if kp is not None and kp[1] == "":
            return (kind, kp[0])
    if t.startswith("else"):
        rest = t[4:].strip()
        if rest == "":
            return ("else", None)
        kp = _keyword_paren(rest, "if")
        if kp is not None and kp[1] == "" and rest.startswith("if"):
            return ("elseif", kp[0])
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
    ('close', '}', line) tokens. Handles strings, // # /* */ comments,
    ';' and newline terminators, vector-literal braces vs block braces,
    and obvious multi-line continuations."""
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
#  Tree building
# ─────────────────────────────────────────────────────────────────────────────

def parse_program(src):
    """Source text → list of Stmt (with nested SBlock bodies)."""
    src = src.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    tokens = scan(src)
    stmts, i = _parse_block(tokens, 0, closing=False, open_line=None)
    _number(stmts, [0])
    return stmts


def _parse_block(tokens, i, closing, open_line):
    body = []
    n = len(tokens)
    while i < n:
        kind, text, line = tokens[i]
        if kind == "close":
            if closing:
                return body, i + 1
            raise FormulaError("stray '}' — there is no open block to close", line)
        if kind == "open":
            header = _classify_header(text)
            hk, payload = header
            if hk in ("else", "elseif"):
                raise FormulaError("'else' without a matching 'if'", line)
            inner, i = _parse_block(tokens, i + 1, closing=True, open_line=line)
            block = _make_block(hk, payload, inner, line, text)
            if hk == "if":
                i = _parse_else_chain(block, tokens, i)
            body.append(block)
            continue
        # plain statement (possibly an inline if / else)
        stmt, i = _parse_inline(tokens, i)
        body.append(stmt)
    if closing:
        raise FormulaError("block opened here is never closed — add a '}'", open_line)
    return body, i


def _parse_inline(tokens, i):
    kind, text, line = tokens[i]
    if re.match(r"if\s*\(", text):
        kp = _keyword_paren(text, "if")
        if kp is None:
            raise FormulaError("malformed if — expected if (condition) { ... }", line)
        cond, rest = kp
        if not rest:
            raise FormulaError("if (...) needs a body: a statement or { ... }", line)
        inner, _ = _parse_block([("stmt", rest, line)], 0, closing=False, open_line=line)
        block = _make_block("if", cond, inner, line, f"if ({cond})")
        return block, _parse_else_chain(block, tokens, i + 1)
    if re.match(r"else\b", text):
        raise FormulaError("'else' without a matching 'if'", line)
    return parse_simple(text, line), i + 1


def _parse_else_chain(block, tokens, i):
    if i >= len(tokens):
        return i
    kind, text, line = tokens[i]
    if kind == "open":
        hk, payload = _classify_header(text)
        if hk == "else":
            inner, i = _parse_block(tokens, i + 1, closing=True, open_line=line)
            block.orelse = inner
            return i
        if hk == "elseif":
            inner, i = _parse_block(tokens, i + 1, closing=True, open_line=line)
            nested = _make_block("if", payload, inner, line, f"else if ({payload})")
            block.orelse = [nested]
            return _parse_else_chain(nested, tokens, i)
        return i
    if kind == "stmt" and re.match(r"else\b", text):
        rest = text[4:].strip()
        if not rest:
            raise FormulaError("'else' needs a body: a statement or { ... }", line)
        sub_tokens = [("stmt", rest, line)]
        inner, _ = _parse_block(sub_tokens, 0, closing=False, open_line=line)
        block.orelse = inner
        # an inline 'else if (...) stmt' produced a nested if; its own else
        # can only come from the following tokens
        if len(inner) == 1 and isinstance(inner[0], SBlock) and inner[0].kind == "if" \
                and rest.startswith("if"):
            return _parse_else_chain(inner[0], tokens, i + 1)
        return i + 1
    return i


def _make_block(kind, payload, body, line, text):
    if kind == "sim":
        return SBlock(line, "sim", None, body, text=text)
    if kind == "foreach":
        word = payload.lower()
        if word not in FOREACH_DOMAINS:
            raise FormulaError(
                f"unknown foreach domain '{payload}' — use point, edge, face, curve or instance",
                line)
        return SBlock(line, "foreach", FOREACH_DOMAINS[word], body, text=text)
    if kind == "repeat":
        if not payload.strip():
            raise FormulaError("repeat needs a count, e.g. repeat(5) { ... }", line)
        return SBlock(line, "repeat", parse_expr(payload, line), body, text=text)
    if kind == "if":
        if not payload.strip():
            raise FormulaError("if needs a condition, e.g. if (v@position.z > 0) { ... }", line)
        return SBlock(line, "if", parse_expr(payload, line), body, text=text)
    raise FormulaError(f"unexpected block '{text}'", line)


def _number(stmts, counter):
    for s in stmts:
        s.sid = counter[0]
        counter[0] += 1
        if isinstance(s, SBlock):
            _number(s.body, counter)
            if s.orelse:
                _number(s.orelse, counter)
            s.end_sid = counter[0] - 1


# ─────────────────────────────────────────────────────────────────────────────
#  Simple statements
# ─────────────────────────────────────────────────────────────────────────────

_AUG_OPS = {
    "+": ast.Add, "-": ast.Sub, "*": ast.Mult, "/": ast.Div,
    "%": ast.Mod, "^": ast.Pow, "**": ast.Pow,
}


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


def parse_simple(text, line):
    t = text.strip()

    m = re.fullmatch(r"return\b\s*(.*)", t, re.S)
    if m:
        if not m.group(1).strip():
            raise FormulaError("return needs a value, e.g. return length(v@position)", line)
        return SReturn(line, parse_expr(m.group(1), line), t)

    m = re.fullmatch(r"(float|vector3|vector|int|bool)\s+([A-Za-z_]\w*)\s*(?:=\s*(.*))?", t, re.S)
    if m:
        if m.group(3) is not None and not m.group(3).strip():
            raise FormulaError(f"'{m.group(2)}' has '=' but no value", line)
        if m.group(3) is not None and re.match(r"\s*=", m.group(3)):
            raise FormulaError("use '=' to declare a variable, not '=='", line)
        value = parse_expr(m.group(3), line) if m.group(3) is not None else None
        return SDecl(line, _TYPE_WORDS[m.group(1)], m.group(2), value, t)
    if re.match(r"(float|vector3|vector|int|bool)\s+[A-Za-z_]\w*\s*,", t):
        raise FormulaError("declare one variable per statement (float a = 1; float b = 2;)", line)

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

    return SExpr(line, parse_expr(t, line), t)


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


# ─────────────────────────────────────────────────────────────────────────────
#  Expression preprocessing: VEX-flavoured text → Python source
# ─────────────────────────────────────────────────────────────────────────────

ATTR_PREFIX = "_attr_"
_PREFIXES = "fivbs"
_ATTR_RE = re.compile(r"(?<![\w@])([fivbs]?)@([A-Za-z_]\w*)")
_STR_RE = re.compile(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'")


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
    everywhere, including inside brackets and function arguments."""
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
    for i, c in enumerate(s):
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == sep and depth == 0:
            parts.append(s[start:i])
            start = i + 1
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
    code = code.replace("&&", " and ").replace("||", " or ")
    code = re.sub(r"!(?!=)", " not ", code)
    code = code.replace("^", "**")
    code = _ATTR_RE.sub(lambda m: attr_placeholder(m.group(1), m.group(2)), code)
    if "@" in code:
        bad = re.search(r"\S*@\S*", code).group(0)
        raise FormulaError(
            f"can't read '{bad}' — attributes look like @name, f@name, v@name, i@name or b@name")
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
    except SyntaxError as e:
        hint = ""
        if re.search(r"\b(for|while|do)\b", text):
            hint = " — loops aren't supported; use repeat(n) { } or foreach { }"
        elif "{" in text:
            hint = " — vector literals need exactly three values: {x, y, z}"
        raise FormulaError(f"syntax error in '{text.strip()}'{hint}", line) from None
    return tree.body
