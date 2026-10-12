# SPDX-License-Identifier: GPL-3.0-or-later
"""Maintainer helper: copy this checkout's add-on over the installed extension
and reload it plus the promo modules, in the running Blender.

    import devsync; devsync.reload({"formula_to_nodes/recipes.py": "<md5>"})
"""

import hashlib
import importlib
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MOD = "bl_ext.blender_org.formula_to_nodes"


def check(expected):
    bad = []
    for f, h in (expected or {}).items():
        p = os.path.join(ROOT, f)
        if not os.path.exists(p) or hashlib.md5(open(p, "rb").read()).hexdigest() != h:
            bad.append(f)
    return bad


def reload(expected=None, install=True):
    import addon_utils
    bad = check(expected)
    if bad:
        return {"ok": False, "mismatch": bad}
    inst = None
    if install and MOD in sys.modules:
        inst = os.path.dirname(sys.modules[MOD].__file__)
        addon_utils.disable(MOD, default_set=False)
    for k in [k for k in sys.modules if (inst and (k == MOD or k.startswith(MOD + "."))) or k == "formula_to_nodes"
              or k.startswith("formula_to_nodes.") or k in ("sims", "live_promo")]:
        del sys.modules[k]
    if inst:
        shutil.copytree(os.path.join(ROOT, "formula_to_nodes"), inst, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__"))
        addon_utils.enable(MOD, default_set=True)
    import live_promo  # noqa: F401
    return {"ok": True, "installed": inst, "version": getattr(sys.modules.get(MOD), "ADDON_VERSION", None)}
