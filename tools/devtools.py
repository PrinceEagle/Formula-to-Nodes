# SPDX-License-Identifier: GPL-3.0-or-later
"""Maintainer helpers for running the test suite and promo renders in a
separate background Blender, so the Blender you're working in is never
touched (no objects, node groups or scenes are added to your file).

From Blender's Python console (or any MCP client that runs Python in Blender):

    import sys; sys.path.insert(0, "/path/to/formula-to-nodes/tools")
    import devtools
    devtools.run_tests()                      # whole suite, waits (a few seconds)
    job = devtools.launch("promo/sims.py", "--shot", "smoke")   # returns at once
    devtools.status(job)                      # tail of the job's log, running or not
"""

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(ROOT, "promo", "logs")


def _blender():
    try:
        import bpy
        return bpy.app.binary_path
    except ImportError:
        return os.environ.get("BLENDER", "blender")


def run_tests(pattern="test_*.py", names=None, verbosity=1, timeout=600):
    tests = os.path.join(ROOT, "tests")
    load = (f"unittest.defaultTestLoader.loadTestsFromNames({names!r})" if names
            else f"unittest.defaultTestLoader.discover({tests!r}, pattern={pattern!r})")
    expr = (f"import sys, unittest; sys.path[:0] = [{ROOT!r}, {tests!r}];"
            f"r = unittest.TextTestRunner(verbosity={verbosity}).run({load});"
            f"sys.exit(0 if r.wasSuccessful() else 1)")
    t = time.time()
    p = subprocess.run([_blender(), "-b", "--factory-startup", "--python-exit-code", "1", "--python-expr", expr],
                       capture_output=True, text=True, timeout=timeout)
    out = p.stdout + p.stderr
    i = out.find("Blender ")
    return {"ok": p.returncode == 0, "seconds": round(time.time() - t, 1), "output": out[max(i, 0):][-8000:]}


# kept outside this module so reloading devtools doesn't forget running jobs
_jobs = sys.modules.setdefault("_ftn_devtools_jobs", type(sys)("_ftn_devtools_jobs")).__dict__.setdefault("jobs", {})


def md5s(files):
    """{path: md5} of files under the repo (to check a copy arrived intact)."""
    import hashlib
    out = {}
    for f in files:
        p = os.path.join(ROOT, f)
        out[f] = hashlib.md5(open(p, "rb").read()).hexdigest() if os.path.exists(p) else None
    return out


def mismatched(expected):
    got = md5s(list(expected))
    return [f for f, h in expected.items() if got[f] != h]


def launch(script, *args, name=None):
    """Start ``blender -b --factory-startup -P script -- args`` in the background."""
    os.makedirs(LOGS, exist_ok=True)
    name = name or f"{os.path.splitext(os.path.basename(script))[0]}-{int(time.time())}"
    log = os.path.join(LOGS, name + ".log")
    path = script if os.path.isabs(script) else os.path.join(ROOT, script)
    f = open(log, "w")
    p = subprocess.Popen([_blender(), "-b", "--factory-startup", "-P", path, "--", *args],
                         stdout=f, stderr=subprocess.STDOUT, cwd=ROOT, start_new_session=True)
    _jobs[name] = (p, log, time.time())
    return name


def status(name, tail=3000):
    if name not in _jobs:
        log = os.path.join(LOGS, name + ".log")
        text = open(log).read() if os.path.exists(log) else ""
        return {"name": name, "running": None, "returncode": None, "seconds": None, "log": text[-tail:]}
    p, log, t0 = _jobs[name]
    rc = p.poll()
    try:
        with open(log) as f:
            text = f.read()
    except OSError:
        text = ""
    return {"name": name, "running": rc is None, "returncode": rc, "seconds": round(time.time() - t0, 1),
            "log": text[-tail:]}


def jobs():
    return {k: status(k, tail=300) for k in _jobs}


def stop(name):
    p, _log, _t = _jobs[name]
    if p.poll() is None:
        p.terminate()
    return status(name)
