# SPDX-License-Identifier: GPL-3.0-or-later
"""Your own presets: scripts saved by name in Blender's user config folder
(…/config/formula_to_nodes/presets.json), shown under Recipes › My Presets in every file."""

import json
import os
import time

_cache = {"mtime": None, "data": []}


def _path():
    import bpy
    folder = bpy.utils.user_resource("CONFIG", path="formula_to_nodes", create=True)
    return os.path.join(folder, "presets.json")


def load():
    """[{name, script, saved}] sorted by name. A broken file is kept aside, never thrown away."""
    path = _path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        _cache.update(mtime=None, data=[])
        return []
    if mtime == _cache["mtime"]:
        return _cache["data"]
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        items = [d for d in data.get("presets", []) if isinstance(d, dict) and d.get("name") and "script" in d]
    except (OSError, ValueError):
        try:
            os.replace(path, path + f".broken-{int(time.time())}")
        except OSError:
            pass
        items = []
    items.sort(key=lambda d: d["name"].lower())
    _cache.update(mtime=mtime, data=items)
    return items


def _write(items):
    path = _path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "presets": items}, f, indent=1, ensure_ascii=False)
    if os.path.exists(path):
        try:
            os.replace(path, path + ".bak")
        except OSError:
            pass
    os.replace(tmp, path)
    _cache["mtime"] = None


def get(name):
    return next((d for d in load() if d["name"] == name), None)


def save(name, script):
    name = name.strip()
    if not name:
        raise ValueError("give the preset a name")
    items = [d for d in load() if d["name"] != name]
    items.append({"name": name, "script": script, "saved": time.strftime("%Y-%m-%d %H:%M")})
    _write(items)
    return name


def delete(name):
    items = [d for d in load() if d["name"] != name]
    _write(items)
