#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Formula to Nodes MCP server (stdio).

Claude Desktop / Claude Code launch this with Blender's bundled Python:

    <blender python> mcp_server.py [--host 127.0.0.1] [--port 9876]

Language tools (guide, check) run right here, so they work even when
Blender is closed. Scene tools are forwarded to Blender through Blender
Lab's "MCP" bridge add-on (a TCP socket inside Blender, port 9876 by
default), which runs them on Blender's main thread.

Standard library only. Logs go to stderr — stdout is the protocol.
"""

import argparse
import importlib.util
import json
import os
import socket
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.basename(HERE)
SERVER_NAME = "formula-to-nodes"


def _load_package():
    """Import this add-on's pure-Python modules without bpy."""
    spec = importlib.util.spec_from_file_location(PKG, os.path.join(HERE, "__init__.py"),
                                                  submodule_search_locations=[HERE])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = pkg
    spec.loader.exec_module(pkg)
    return (importlib.import_module(PKG + ".compiler"), importlib.import_module(PKG + ".ai"),
            importlib.import_module(PKG + ".lang"))


compiler, ai, lang = _load_package()
VERSION = getattr(sys.modules[PKG], "ADDON_VERSION", "2")


def log(*a):
    print("[formula-to-nodes]", *a, file=sys.stderr, flush=True)


# ─────────────────────────────────────────────────────────────────────────────
#  Blender bridge (Blender Lab MCP add-on protocol)
# ─────────────────────────────────────────────────────────────────────────────

class BridgeError(Exception):
    pass


_CALL_TEMPLATE = """\
import sys, json
_api = next((m for n, m in list(sys.modules.items()) if n.endswith("formula_to_nodes.api")), None)
if _api is None:
    result = {{"ok": False, "error": "The Formula to Nodes add-on isn't enabled in this Blender session."}}
else:
    result = _api.{fn}(**json.loads({args}))
"""


class Bridge:
    def __init__(self, host, port):
        self.host = "127.0.0.1" if host in ("localhost", "") else host
        self.port = port

    def call(self, fn, timeout=120, **kwargs):
        code = _CALL_TEMPLATE.format(fn=fn, args=repr(json.dumps(kwargs)))
        payload = (json.dumps({"type": "execute", "code": code, "strict_json": True}) + "\0").encode("utf-8")
        try:
            sock = socket.create_connection((self.host, self.port), timeout=5)
        except OSError:
            raise BridgeError(
                f"Can't reach Blender on {self.host}:{self.port}. In Blender: install and enable the "
                f"'MCP' add-on (Blender Lab), turn on Preferences › System › Network › Allow Online Access, "
                f"and make sure its server is started (Preferences › Add-ons › MCP › Start). "
                f"Also enable Formula to Nodes.") from None
        try:
            sock.settimeout(timeout)
            sock.sendall(payload)
            buf = bytearray()
            while not buf.endswith(b"\0"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
        except socket.timeout:
            raise BridgeError(f"Blender didn't answer within {timeout} s (is it busy or showing a dialog?)") from None
        finally:
            sock.close()
        if not buf:
            raise BridgeError("Blender closed the connection without answering")
        resp = json.loads(bytes(buf).rstrip(b"\0").decode("utf-8"))
        if resp.get("status") != "ok":
            raise BridgeError("Blender reported an error:\n" + str(resp.get("message", resp)))
        return resp.get("result", {})


# ─────────────────────────────────────────────────────────────────────────────
#  Tools
# ─────────────────────────────────────────────────────────────────────────────

INSTRUCTIONS = (
    "Formula to Nodes turns short VEX-like scripts into Blender Geometry Nodes groups. "
    "Before writing a script, call formula_language_guide once. Then: formula_check_script → "
    "formula_build (optionally applying it to an object) → formula_inspect to verify the evaluated "
    "attribute values → tweak with formula_set_parameters or rebuild with update_group. "
    "Prefer chf()/chi()/chv() parameters over magic numbers so the user can tweak results."
)

TOOLS = [
    {"name": "formula_language_guide",
     "description": "The complete Formula to Nodes language reference (syntax, attributes, every "
                    "function, examples). Call this before writing your first script.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "formula_check_script",
     "description": "Compile a script without touching Blender. Returns errors with line numbers, "
                    "warnings, node count and the parameters it would create. Works even if Blender is closed.",
     "inputSchema": {"type": "object", "properties": {
         "script": {"type": "string", "description": "Formula to Nodes script"}},
         "required": ["script"]}},
    {"name": "formula_build",
     "description": "Build a Geometry Nodes group in the open Blender file from a script. To change an "
                    "existing group in place (keeping its links and the user's modifier values), pass its "
                    "name as update_group. Pass object (a name, or 'active') to add it as a modifier.",
     "inputSchema": {"type": "object", "properties": {
         "script": {"type": "string"},
         "name": {"type": "string", "description": "Name for a new group (default 'Formula')"},
         "update_group": {"type": "string", "description": "Existing Formula group to rebuild in place"},
         "object": {"type": "string", "description": "Object to apply it to as a modifier, or 'active'"}},
         "required": ["script"]}},
    {"name": "formula_inspect",
     "description": "Read evaluated attribute values of an object after its modifiers (position, your "
                    "custom attributes...) plus any node warnings. Use it to verify a build did what you meant.",
     "inputSchema": {"type": "object", "properties": {
         "object": {"type": "string", "description": "Object name or 'active'"},
         "attributes": {"type": "array", "items": {"type": "string"},
                        "description": "Only these attributes (default: all)"},
         "limit": {"type": "integer", "description": "Values per attribute (default 8, max 200)"}},
         "required": ["object"]}},
    {"name": "formula_set_parameters",
     "description": "Set the parameter values (chf/chi/chv sliders) on an object's Formula modifier.",
     "inputSchema": {"type": "object", "properties": {
         "object": {"type": "string"},
         "values": {"type": "object", "description": "{parameter name: value}; vectors as [x, y, z]"},
         "modifier": {"type": "string", "description": "Modifier name if the object has several"}},
         "required": ["object", "values"]}},
    {"name": "formula_list_groups",
     "description": "List the Formula to Nodes groups in the Blender file with their parameters and users.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "formula_get_source",
     "description": "Get the script of an existing Formula group, to edit it and rebuild with update_group.",
     "inputSchema": {"type": "object", "properties": {"group": {"type": "string"}}, "required": ["group"]}},
    {"name": "blender_scene_overview",
     "description": "Objects in the current scene (type, selection, modifiers) and the Blender version.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def _check(script):
    try:
        res = compiler.compile_source(script, "SCRIPT")
    except lang.FormulaError as e:
        return {"ok": False, "error": str(e), "line": e.line}
    params = [{"name": s.name, "type": s.vtype.lower(), "default": s.default}
              for s in res.iface if s.key.startswith("param:") and s.vtype != "GEOMETRY"]
    outputs = [s.name for s in res.iface if s.in_out == "OUTPUT"]
    return {"ok": True, "nodes": res.node_count, "parameters": params, "outputs": outputs, "notes": res.notes}


def call_tool(bridge, name, args):
    """Returns (data, is_error)."""
    if name == "formula_language_guide":
        return ai.build_system_prompt(for_tools=True), False
    if name == "formula_check_script":
        r = _check(args.get("script", ""))
        return r, not r["ok"]
    if name == "formula_build":
        script = args.get("script", "")
        pre = _check(script)
        if not pre["ok"]:
            return pre, True
        r = bridge.call("build_group", script=script, name=args.get("name") or "Formula",
                        update_group=args.get("update_group") or "", object_name=args.get("object") or "")
        return r, not r.get("ok", False)
    if name == "formula_inspect":
        r = bridge.call("inspect", object_name=args.get("object", ""), attributes=args.get("attributes") or None,
                        limit=int(args.get("limit") or 8))
        return r, not r.get("ok", False)
    if name == "formula_set_parameters":
        r = bridge.call("set_parameters", object_name=args.get("object", ""), values=args.get("values") or {},
                        modifier=args.get("modifier") or "")
        return r, not r.get("ok", False)
    if name == "formula_list_groups":
        return bridge.call("list_groups"), False
    if name == "formula_get_source":
        r = bridge.call("group_source", group=args.get("group", ""))
        return r, not r.get("ok", False)
    if name == "blender_scene_overview":
        return bridge.call("scene_overview"), False
    raise KeyError(name)


# ─────────────────────────────────────────────────────────────────────────────
#  JSON-RPC over stdio
# ─────────────────────────────────────────────────────────────────────────────

class Server:
    def __init__(self, bridge, out=None):
        self.bridge = bridge
        self.out = out or sys.stdout.buffer

    def send(self, msg):
        self.out.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
        self.out.flush()

    def handle(self, msg):
        method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if mid is None:
            return None  # notification
        if method == "initialize":
            return {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": VERSION},
                    "instructions": INSTRUCTIONS}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            try:
                data, is_error = call_tool(self.bridge, name, args)
            except KeyError:
                raise _RpcError(-32602, f"unknown tool: {name}")
            except BridgeError as e:
                data, is_error = str(e), True
            except Exception:
                log(traceback.format_exc())
                data, is_error = "Internal error:\n" + traceback.format_exc(limit=3), True
            text = data if isinstance(data, str) else json.dumps(data, indent=1, ensure_ascii=False)
            return {"content": [{"type": "text", "text": text}], "isError": is_error}
        if method in ("resources/list", "prompts/list"):
            return {method.split("/")[0]: []}
        raise _RpcError(-32601, f"method not found: {method}")

    def serve(self, stream=None):
        stream = stream or sys.stdin.buffer
        log(f"ready (Blender bridge {self.bridge.host}:{self.bridge.port})")
        for raw in iter(stream.readline, b""):
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self.send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
                continue
            if not isinstance(msg, dict):
                continue
            try:
                result = self.handle(msg)
                if msg.get("id") is not None:
                    self.send({"jsonrpc": "2.0", "id": msg["id"], "result": result})
            except _RpcError as e:
                self.send({"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": e.code, "message": e.msg}})


class _RpcError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def main(argv=None):
    p = argparse.ArgumentParser(description="Formula to Nodes MCP server (stdio)")
    p.add_argument("--host", default=os.environ.get("BLENDER_MCP_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("BLENDER_MCP_PORT", "9876")))
    a = p.parse_args(argv)
    Server(Bridge(a.host, a.port)).serve()


if __name__ == "__main__":
    main()
