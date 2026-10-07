# SPDX-License-Identifier: GPL-3.0-or-later
"""Registers the Formula to Nodes MCP server with Claude Desktop. Pure Python."""

import glob
import json
import os
import shutil
import sys

SERVER_KEY = "formula-to-nodes"
SERVER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")


def python_executable():
    """Blender's bundled Python (sys.executable inside Blender)."""
    return sys.executable


def server_entry(host="127.0.0.1", port=9876, python=None):
    args = [SERVER_SCRIPT]
    if port != 9876:
        args += ["--port", str(port)]
    if host not in ("127.0.0.1", "localhost", ""):
        args += ["--host", host]
    return {"command": python or python_executable(), "args": args}


def claude_desktop_config_paths(platform=None, env=None, home=None):
    """Every Claude Desktop config file location for this OS (existing or standard)."""
    platform = platform or sys.platform
    env = env if env is not None else os.environ
    home = home or os.path.expanduser("~")
    paths = []
    if platform == "darwin":
        paths.append(os.path.join(home, "Library", "Application Support", "Claude", "claude_desktop_config.json"))
    elif platform.startswith("win"):
        appdata = env.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
        paths.append(os.path.join(appdata, "Claude", "claude_desktop_config.json"))
        local = env.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        # Microsoft Store (MSIX) install keeps its own virtualized copy
        for pkg in glob.glob(os.path.join(local, "Packages", "Claude_*")):
            paths.append(os.path.join(pkg, "LocalCache", "Roaming", "Claude", "claude_desktop_config.json"))
    else:
        cfg = env.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
        paths.append(os.path.join(cfg, "Claude", "claude_desktop_config.json"))
    return paths


def read_config(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8-sig") as f:
        text = f.read()
    if not text.strip():
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path} doesn't contain a JSON object")
    return data


def is_registered(path):
    try:
        return SERVER_KEY in read_config(path).get("mcpServers", {})
    except (OSError, ValueError):
        return False


def register(path, entry):
    """Add/replace our server in a Claude Desktop config, keeping everything else.
    Writes a .bak copy first. Returns a short description of what happened."""
    data = read_config(path)          # raises on invalid JSON: never clobber a file we can't parse
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(f"'mcpServers' in {path} isn't an object")
    existed = SERVER_KEY in servers
    servers[SERVER_KEY] = entry
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return "updated" if existed else "added"


def unregister(path):
    data = read_config(path)
    if SERVER_KEY not in data.get("mcpServers", {}):
        return False
    shutil.copy2(path, path + ".bak")
    del data["mcpServers"][SERVER_KEY]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    return True


def config_snippet(entry):
    return json.dumps({"mcpServers": {SERVER_KEY: entry}}, indent=2)


def claude_code_command(entry):
    def q(s):
        return f'"{s}"' if (" " in s or "\\" in s) else s
    return "claude mcp add " + SERVER_KEY + " -- " + " ".join(q(x) for x in [entry["command"]] + entry["args"])
