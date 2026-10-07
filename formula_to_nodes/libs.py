# SPDX-License-Identifier: GPL-3.0-or-later
"""#include resolution (pure Python).

Looks in, in order: extra folders (e.g. the user's snippet folder), then the
libraries bundled in this add-on's include/ folder. Inside Blender, ui.py
also lets a Text datablock with the same name win."""

import os

INCLUDE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "include")
EXTENSIONS = ("", ".h", ".vfl", ".ftn", ".txt")


def _safe(name):
    name = name.strip().replace("\\", "/")
    if not name or name.startswith("/") or ".." in name.split("/"):
        return None
    return name


def bundled():
    """[(name, first comment line)] of the bundled libraries."""
    out = []
    if not os.path.isdir(INCLUDE_DIR):
        return out
    for fn in sorted(os.listdir(INCLUDE_DIR)):
        path = os.path.join(INCLUDE_DIR, fn)
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            first = f.readline().strip().lstrip("/").strip()
        out.append((fn, first))
    return out


def read_from_dirs(name, dirs):
    name = _safe(name)
    if name is None:
        return None
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for ext in EXTENSIONS:
            path = os.path.join(d, name + ext if ext and not name.endswith(ext) else name)
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
    return None


def resolve(name):
    """Default resolver: the bundled libraries only."""
    return read_from_dirs(name, [INCLUDE_DIR])


def make_resolver(lookup=None, dirs=()):
    """Resolver that tries ``lookup(name)`` (e.g. Blender text datablocks),
    then ``dirs``, then the bundled libraries."""
    def _resolve(name):
        if lookup is not None:
            text = lookup(name)
            if text is not None:
                return text
        return read_from_dirs(name, list(dirs) + [INCLUDE_DIR])
    return _resolve
