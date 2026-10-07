# SPDX-License-Identifier: GPL-3.0-or-later
# Formula to Nodes — Author: Prince Eagle
# Location: Geometry Nodes Editor > Sidebar (N) > Formula
#
# VEX-style formulas and scripts → tidy Geometry Nodes groups, with an
# optional AI assistant whose output is compiled before it's shown.
#
# Blender Extension: metadata lives in blender_manifest.toml (no bl_info).
#
# Modules
#   lang.py      source text → statement tree            (pure Python)
#   compiler.py  statements → typed node-graph IR        (pure Python)
#   layout.py    IR → tidy node positions                 (pure Python)
#   ai.py        prompt, HTTP client, validating retries  (pure Python)
#   examples.py  example scripts
#   build.py     IR → real node tree (bpy)
#   ui.py        panels, operators, preferences (bpy)
#   api.py       Blender-side functions for MCP clients (bpy)
#   mcp_server.py  stdio MCP server for Claude Desktop / Claude Code
#   mcp_setup.py   writes the Claude Desktop config

import os

ADDON_VERSION = "2.1.0"


def _read_version():
    try:
        import tomllib
        path = os.path.join(os.path.dirname(__file__), "blender_manifest.toml")
        with open(path, "rb") as f:
            return tomllib.load(f).get("version", ADDON_VERSION)
    except Exception:
        return ADDON_VERSION


try:
    import bpy  # noqa: F401
except ImportError:      # mcp_server.py imports the pure-Python modules outside Blender
    bpy = None

if bpy is not None:
    if "ui" in locals():
        import importlib
        for _m in (lang, compiler, layout, ai, examples, build, mcp_setup, ui, api):  # noqa: F821
            importlib.reload(_m)
    else:
        from . import lang, compiler, layout, ai, examples, build, mcp_setup, ui, api


def register():
    ui.VERSION = _read_version()
    ui.register()


def unregister():
    ui.unregister()
