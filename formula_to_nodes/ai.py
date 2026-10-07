# SPDX-License-Identifier: GPL-3.0-or-later
"""AI Assist: natural language → Formula to Nodes script.

No bpy import: runs in a worker thread. Every model reply is compiled with
the real compiler; compile errors are sent back to the model for a fix, so
what reaches the user has already been proven to build.
"""

import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

from . import caps, compiler, examples, libs, recipes

PROVIDERS = {
    "ANTHROPIC": {
        "label": "Anthropic (Claude)", "wire": "anthropic",
        "url": "https://api.anthropic.com/v1/messages",
        "model": "claude-sonnet-5-5", "env": "ANTHROPIC_API_KEY", "needs_key": True,
    },
    "OPENAI": {
        "label": "OpenAI", "wire": "openai",
        "url": "https://api.openai.com/v1/chat/completions",
        "model": "", "env": "OPENAI_API_KEY", "needs_key": True,
    },
    "OLLAMA": {
        "label": "Ollama (local)", "wire": "openai",
        "url": "http://localhost:11434/v1/chat/completions",
        "model": "", "env": "", "needs_key": False,
    },
    "CUSTOM": {
        "label": "OpenAI-compatible (custom URL)", "wire": "openai",
        "url": "", "model": "", "env": "", "needs_key": False,
    },
}


class AIError(Exception):
    pass


class Config:
    def __init__(self, provider, api_key="", model="", url="", timeout=180, max_attempts=3):
        info = PROVIDERS[provider]
        self.provider = provider
        self.label = info["label"]
        self.wire = info["wire"]
        self.url = (url or info["url"]).strip()
        self.model = (model or info["model"]).strip()
        self.api_key = api_key.strip()
        self.needs_key = info["needs_key"]
        self.env = info["env"]
        self.timeout = timeout
        self.max_attempts = max(1, int(max_attempts))

    @property
    def is_local(self):
        host = (urllib.parse.urlparse(self.url).hostname or "").lower()
        return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local")

    def problem(self):
        if not self.url:
            return "Set an endpoint URL in the add-on preferences"
        if not self.url.startswith(("http://", "https://")):
            return "The endpoint URL must start with http:// or https://"
        if not self.model:
            return f"Set a model name for {self.label} in the add-on preferences"
        if self.needs_key and not self.api_key:
            hint = f" (or set the {self.env} environment variable)" if self.env else ""
            return f"Add your {self.label} API key in the add-on preferences{hint}"
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  Prompt
# ─────────────────────────────────────────────────────────────────────────────

_RULES = """\
You write scripts in the "Formula to Nodes" language. It is VEX-like (Houdini
wrangles) and compiles into a Blender Geometry Nodes group.

OUTPUT FORMAT
- Reply with exactly one fenced code block containing the whole script, nothing else.

WHERE LINES RUN (like a wrangle's Run Over menu)
- By default every line runs once per point. Put one of these on the first line to change it:
  #runover prim (faces)   #runover vertex (face corners)   #runover edge   #runover curve
  #runover instance        #runover detail (once for the whole geometry)
- runover(prim) { ... } runs a block over another domain. Values read across domains are averaged.
- @ptnum @primnum @vtxnum @elemnum: element numbers. @numpt @numprim @numvtx @numelem: counts.

STATEMENTS (end with ';' or a newline; // and /* */ comments)
- Attribute write:   f@name = expr;   @P += expr;   @P.z = expr;   i@count++;
- Locals:            float a = expr;  vector v = {0, 0, 1};  int i = 0;  bool b = true;
                     vector4 q = quaternion(angle, axis);  matrix m = ident();  string s = "x";
                     float w[] = {1, 2, 3};   (arrays are one value for the whole geometry)
- if (cond) { ... } else if (cond) { ... } else { ... }      cond ? a : b
- for (int i = 0; i < n; i++) { ... }   foreach (float x; array) { ... }   (no while/break)
- repeat(n) { ... }      repeat zone; `iteration` inside
- simulate { init { ... } ... }   simulation zone; init runs on the first frame; `deltatime` inside.
  Variables changed inside repeat/for/simulate keep their new values after the block.
- foreach(point|prim|edge|vertex|curve|instance) { ... }  per-element zone; `elemindex` inside
- return expr;   optional, last line only: adds a "Result" field output
- Functions (define before use, inlined; arguments are passed by reference like VEX):
    float falloff(float d; float r) { return clamp(1 - d / r, 0, 1); }
- #include "falloff.h" "sdf.h" "shaping.h" "color.h" "noise.h" (bundled libraries), #define NAME 1.5

ATTRIBUTES
- Prefixes: f@ float, i@ int, b@ bool, v@ vector, p@ rotation (quaternion), 4@ matrix,
  c@ colour, u@ 2D vector. Always prefix custom attributes.
- Built-ins: @P @N @id @pscale (radius) @Cd (colour) @uv @orient @scale @rest @v (velocity)
  @Time @Frame; read-only: @area @island @edgeangle @curveparam @curvelength @tangent.
- Writable: @P @N @id @pscale @Cd @uv @orient @scale @material_index f@tilt.
- @opinput1_P reads the same attribute of the same element from the "Input 1" object.
- Detail (whole-geometry) attributes: in #runover detail, f@total = ... stores one value;
  read it anywhere with detail(0, "total"); combine per-element values with
  setdetailattrib(0, "total", value, "add" | "min" | "max" | "mean").

TYPES & OPERATORS
- + - * / % ** (vector * float scales). rotation * rotation combines, rotation * vector rotates,
  vector * matrix transforms a position. < <= > >= == != && || !
- A vector can't be used where a float is expected: use .x/.y/.z, length() or dot().
- Constants: pi, e, tau, true, false.

GEOMETRY CHANGES (statements on their own line)
- removepoint(0, @ptnum), removeprim(0, @primnum, 1), addpoint(0, pos): applied when the script ends.
- scatter(density, seed, mindist) → later lines run on the new points, which have @N and p@orient.
- instance(chobj("x") | chcoll("x") | 1, index) copies onto points using p@orient, v@scale, @pscale;
  later lines run over the instances (@P, @orient, @scale move them). realize() makes them real.
- subdivide(n) subdivsurf(n) triangulate() dualmesh() extrude(d) bevel(width, segments) fuse(dist)
  resample(n) sweep(radius) tocurves() topoints() points(n) grid(sx, sy, nx, ny) join(1)
  transform(t, r, s) setmaterial(chmat("m")) shadesmooth(1). Inside an if they apply to the
  selected elements only (where the operation has a selection).

PARAMETERS (become inputs on the node and the modifier — prefer them over magic numbers)
- chf("name", default, min=, max=, tip="tooltip")  chi()  chv("name", {x, y, z})  chb()  chs()  chp()
- "Folder/name" puts the input in a collapsible panel: chf("Shape/height", 1)
- Pickers: chobj("Target") chcoll("Rocks") chmat("Paint") chimg("Map") chsound("Music")
- chramp("profile", t, "smooth") is a curve the user shapes in the sidebar; a colour ramp when
  assigned to a vector, or colorramp("colors", t, "fire").
- Reuse a name to reuse a parameter. Give every parameter a sensible default.

LOCALS HAVE VALUE SEMANTICS: a local that reads @P keeps that value even after later lines
write @P (the compiler captures it). After scatter()/instance()/subdivide() etc. the elements
changed: per-element locals from before can't be used — store them in attributes first.

NOT AVAILABLE: while/do loops, break/continue, per-element arrays (neighbours(), nearpoints()),
array attributes (f[]@), faces built point by point (addprim), recursion.
"""


def build_system_prompt(for_tools=False, target=None):
    target = target if isinstance(target, caps.Target) else caps.Target(target)
    rules = _RULES
    if for_tools:
        rules = re.sub(r"OUTPUT FORMAT\n.*?\n\n", "", rules, flags=re.S)
        rules += ("\nWORKFLOW WITH TOOLS\n- Pass the script to formula_check_script / formula_build "
                  "as plain text (no code fences).\n- After building, verify with formula_inspect.\n"
                  "- formula_list_recipes / formula_get_recipe give tested starting points.\n")
    lines = [rules, f"TARGET: Blender {caps.version_str(target.version)}."]
    missing = [label for key, (need, label, _n) in caps.FEATURES.items() if not target.supports(key)]
    if missing:
        lines.append("Not available in this version: " + "; ".join(missing) + ".")
    lines += ["", "FUNCTIONS"]
    for cat, fds in compiler.reference_by_category(target).items():
        if not fds:
            continue
        lines.append(f"{cat}:")
        for fd in fds:
            lines.append(f"  {fd.sig}  — {fd.doc}")
    lines.append("")
    lines.append("INCLUDE LIBRARIES: " + "; ".join(f"{name} ({desc})" for name, desc in libs.bundled()))
    lines.append("")
    lines.append("EXAMPLES")
    for key in examples.AI_FEW_SHOT:
        ex = examples.get(key)
        if ex:
            lines.append(f"Request: {ex[1]} — {ex[2]}\n```\n{ex[3]}\n```")
    return "\n".join(lines)


def build_user_message(request, current_script=None, error=None):
    parts = []
    if current_script and current_script.strip():
        parts.append("Current script:\n```\n" + current_script.strip() + "\n```")
        if error:
            parts.append(f"It currently fails to compile with:\n{error}")
        parts.append("Change it as follows: " + (request.strip() or "fix the error"))
    else:
        parts.append("Write a script for: " + request.strip())
    return "\n\n".join(parts)


_FENCE = re.compile(r"```[ \t]*([A-Za-z0-9_+-]*)[ \t]*\n(.*?)```", re.S)


def extract_script(text):
    """The code from the reply's fenced block (the longest if several)."""
    if not text:
        return ""
    blocks = [m.group(2) for m in _FENCE.finditer(text)]
    if blocks:
        return max(blocks, key=len).strip()
    if "```" in text:  # unterminated fence (truncated reply)
        return text.split("```", 1)[1].split("\n", 1)[-1].strip()
    return text.strip()


# ─────────────────────────────────────────────────────────────────────────────
#  HTTP
# ─────────────────────────────────────────────────────────────────────────────

def _post(url, headers, body, timeout):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            payload = json.loads(e.read().decode("utf-8", "replace"))
            err = payload.get("error", payload)
            detail = err.get("message", "") if isinstance(err, dict) else str(err)
        except Exception:
            pass
        hints = {401: "the API key was rejected", 403: "access denied for this key/model",
                 404: "endpoint or model not found — check the URL and model name",
                 429: "rate limited or out of credits — try again shortly"}
        msg = hints.get(e.code, f"HTTP {e.code}")
        raise AIError(f"{msg}{': ' + detail if detail else ''}") from None
    except urllib.error.URLError as e:
        raise AIError(f"couldn't reach {urllib.parse.urlparse(url).netloc}: {e.reason}") from None
    except TimeoutError:
        raise AIError(f"no reply within {timeout} s — try again or raise the timeout") from None
    except json.JSONDecodeError:
        raise AIError("the server's reply wasn't valid JSON") from None


# models that accept server-side "fallbacks": "default" (Claude API only)
_DEFAULT_FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"}


def _supports_default_fallback(cfg):
    host = (urllib.parse.urlparse(cfg.url).hostname or "").lower()
    return host == "api.anthropic.com" and cfg.model in _DEFAULT_FALLBACK_MODELS


def complete(cfg, system, messages):
    """Send a chat and return the reply text."""
    if cfg.wire == "anthropic":
        headers = {"x-api-key": cfg.api_key, "anthropic-version": "2023-06-01"}
        body = {"model": cfg.model, "max_tokens": 16000, "system": system, "messages": messages}
        if _supports_default_fallback(cfg):
            # on a safety-classifier decline the API retries on Anthropic's recommended model
            headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
            body["fallbacks"] = "default"
        data = _post(cfg.url, headers, body, cfg.timeout)
        if data.get("stop_reason") == "refusal":
            details = data.get("stop_details") or {}
            cat = details.get("category") if isinstance(details, dict) else None
            raise AIError("the model declined this request" + (f" ({cat})" if cat else "") +
                          " — rephrase it, or pick another model in the add-on preferences")
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    else:
        headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
        body = {"model": cfg.model, "messages": [{"role": "system", "content": system}] + messages}
        data = _post(cfg.url, headers, body, cfg.timeout)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise AIError("unexpected reply format from the server") from None
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        text = content or ""
    if not text.strip():
        raise AIError("the model returned an empty reply")
    return text


# ─────────────────────────────────────────────────────────────────────────────
#  Job
# ─────────────────────────────────────────────────────────────────────────────

class Job(threading.Thread):
    """Ask → extract → compile → (retry with the error) until it compiles."""

    def __init__(self, cfg, request, current_script=None, current_error=None, send=complete, target=None,
                 resolver=None):
        super().__init__(daemon=True)
        self.target, self.resolver = target, resolver
        self.cfg, self.request = cfg, request
        self.current_script, self.current_error = current_script, current_error
        self._send = send
        self.status = "Starting…"
        self.attempt = 0
        self.script = ""          # latest script (even if it failed)
        self.error = None         # final failure message, if any
        self.error_line = None
        self.notes = []
        self.log = []             # [(attempt, message)]
        self.done = False
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def run(self):
        try:
            self._run()
        except AIError as e:
            self.error = str(e)
        except Exception as e:     # never let the thread die silently
            self.error = f"{type(e).__name__}: {e}"
        finally:
            self.done = True

    def _run(self):
        system = build_system_prompt(target=self.target)
        messages = [{"role": "user", "content": build_user_message(
            self.request, self.current_script, self.current_error)}]
        for attempt in range(1, self.cfg.max_attempts + 1):
            if self.cancelled:
                return
            self.attempt = attempt
            self.status = f"Asking {self.cfg.label}… ({attempt}/{self.cfg.max_attempts})"
            reply = self._send(self.cfg, system, messages)
            if self.cancelled:
                return
            script = extract_script(reply)
            self.status = f"Checking the script… ({attempt}/{self.cfg.max_attempts})"
            if not script:
                problem = "the reply contained no script"
                self.log.append((attempt, problem))
                messages += [{"role": "assistant", "content": reply},
                             {"role": "user", "content": "Reply with the complete script in one fenced code block."}]
                continue
            self.script = script
            ok, err, notes = compiler.check(script, "SCRIPT", strict=True, target=self.target,
                                            resolver=self.resolver)
            if ok:
                self.notes, self.error, self.error_line = notes, None, None
                self.status = "Done"
                return
            self.error, self.error_line = str(err), err.line
            self.log.append((attempt, str(err)))
            messages += [{"role": "assistant", "content": reply},
                         {"role": "user", "content":
                          f"The compiler rejected that script:\n{err}\n\n"
                          f"Fix it and reply with the complete corrected script in one code block."}]
        if self.error is None:
            self.error = "the model never returned a script"
