# SPDX-License-Identifier: GPL-3.0-or-later
"""Checks the paper solvers against the physics they claim, in a background Blender 5.3:

    blender -b --factory-startup -P promo/paper_tests.py -- pfm_vs_grid
    blender -b --factory-startup -P promo/paper_tests.py -- bubbles_terminal

Prints one RESULT json line per test."""

import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from formula_to_nodes import build, caps, compiler, recipes  # noqa: E402


def fresh():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    return bpy.context.scene


def obj(name):
    me = bpy.data.meshes.new(name)
    me.from_pydata([(0, 0, 0)], [], [])
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def attach(ob, src, params=None):
    res = compiler.compile_source(src, target=caps.from_blender(bpy))
    tree = build.build_group(res, src, "SCRIPT", name=ob.name)
    mod = ob.modifiers.new("ftn", "NODES")
    mod.node_group = tree
    for k, v in (params or {}).items():
        ident = next(it.identifier for it in tree.interface.items_tree
                     if it.item_type == "SOCKET" and it.in_out == "INPUT" and it.name == k)
        getattr(mod.properties.inputs, ident).value = v
    return res


def probe(target, grid, lo, hi, n=24, vector=True):
    """Samples a grid of target's evaluated geometry on an n³ lattice; returns the values."""
    src = (f'v@s = volumesamplev(chobj("src"), "{grid}", @P);' if vector else
           f'f@s = volumesample(chobj("src"), "{grid}", @P);')
    p = obj("probe_" + grid)
    me = p.data
    me.clear_geometry()
    pts = []
    for i in range(n):
        for j in range(n):
            for k in range(n):
                f = [(i + 0.5) / n, (j + 0.5) / n, (k + 0.5) / n]
                pts.append(tuple(lo[a] + (hi[a] - lo[a]) * f[a] for a in range(3)))
    me.from_pydata(pts, [], [])
    attach(p, src, {"src": target})
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    gs = p.evaluated_get(dg).evaluated_geometry()
    data = gs.mesh.attributes["s"].data
    vals = [tuple(d.vector) for d in data] if vector else [d.value for d in data]
    bpy.data.objects.remove(p)
    return vals


def step_frames(sc, frames):
    sc.frame_set(1)
    t = time.perf_counter()
    for f in range(2, frames + 1):
        sc.frame_set(f)
    return (time.perf_counter() - t) * 1000 / max(frames - 1, 1)


def energy(vals):
    return sum(v[0] * v[0] + v[1] * v[1] + v[2] * v[2] for v in vals) / len(vals)


RING_GRID = """
volumebounds({-1, -1, 0}, {1, 1, 2.5}, chf("h", 0.04));
simulate {
    runover(voxel) {
        vector p = @P - {0, 0, 0.4};
        float disc = smoothstep(0.35, 0.3, length(set(p.x, p.y, 0))) * smoothstep(0.08, 0, abs(p.z));
        if (@Frame <= 1) v@vel = {0, 0, 1} * disc;
    }
    volumeproject("vel");
    volumeadvect("vel", "vel", deltatime);
}
"""


def ring_pfm():
    src = recipes.get("pfm_smoke").script
    # same impulse disc as RING_GRID, no smoke source, no buoyancy
    return src, {"buoyancy": 0.0, "radius": 0.0001, "ring_speed": 1.0, "max": (1, 1, 2.5), "voxel_size": 0.04,
                 "reinit_frames": REINIT, "per_voxel": PPC}


REINIT, PPC = 10, 2


def pfm_vs_grid():
    out = {}
    lo, hi = (-0.9, -0.9, 0.1), (0.9, 0.9, 2.4)
    for label in ("grid_maccormack", "particle_flow_map"):
        sc = fresh()
        sc.frame_start = 1
        ob = obj(label)
        if label == "grid_maccormack":
            res = attach(ob, RING_GRID)
        else:
            src, params = ring_pfm()
            res = attach(ob, src, params)
        e = []
        sc.frame_set(1)
        t = time.perf_counter()
        for f in range(2, 41):
            sc.frame_set(f)
            if f in (4, 20, 40):
                e.append(round(energy(probe(ob, "vel", lo, hi)), 6))
        ms = (time.perf_counter() - t) * 1000 / 39
        out[label] = {"energy": e, "kept": round(e[-1] / max(e[0], 1e-12), 3), "ms_per_frame": round(ms, 1),
                      "nodes": res.node_count}
    return out


KERNEL = "quadratic"


def ring_pfm():
    src = recipes.get("pfm_smoke").script.replace('@P, "cubic")', f'@P, "{G2P}")')
    return src, {"buoyancy": 0.0, "radius": 0.0001, "ring_speed": 1.0, "max": (1, 1, 2.5), "voxel_size": 0.04,
                 "reinit_frames": REINIT, "per_voxel": PPC}


G2P = "cubic"


def pfm_sweep():
    global REINIT, PPC, KERNEL, G2P
    out = {}
    for REINIT, PPC, KERNEL, G2P in ((6, 2, "linear", "linear"), (3, 2, "linear", "linear"),
                                     (6, 2, "linear", "quadratic"), (3, 1, "linear", "linear")):
        r = pfm_vs_grid()
        out[f"reinit{REINIT}_ppc{PPC}_g2p{G2P}"] = r["particle_flow_map"]
        out["grid"] = r["grid_maccormack"]
    return out


def bubbles_terminal():
    """One bubble rising in still water: its slip speed must match the Eq. 36 drag/buoyancy balance,
    and a stream of bubbles must drag the water upward (two-way coupling)."""
    full = recipes.get("weta_bubbles").script
    sim = full.split("// hero surface")[0].replace(
        "        v@ub = alive *", "        f@c_dbg = c; f@V_dbg = V; f@r_dbg = @pscale; v@uw_dbg = uw;\n        v@ub = alive *")
    sim += 'v@slip = v@ub - volumesamplev(0, "vel", @P);\n'
    out = {}
    r = 0.002
    mu, rho_w, rho_b, g = 0.001, 1000.0, 1.0, 9.81
    V = 4 / 3 * math.pi * r ** 3
    # (ρw − ρb) g = μ r π / V · (6 + ρw r u / 2μ) u  →  quadratic in u
    a0 = mu * r * math.pi / V
    A, B, C = a0 * rho_w * r / (2 * mu), 6 * a0, -(rho_w - rho_b) * g
    u_th = (-B + math.sqrt(B * B - 4 * A * C)) / (2 * A)
    for label, count, rate in (("single", 1, 1e6), ("stream", 4000, 3000)):
        sc = fresh()
        ob = obj(label)
        attach(ob, sim, {"particles": count, "bubbles_per_second": rate, "radius_min": r, "radius_max": r})
        sc.frame_set(1)
        t = time.perf_counter()
        for f in range(2, 26):
            sc.frame_set(f)
        ms = (time.perf_counter() - t) * 1000 / 24
        dg = bpy.context.evaluated_depsgraph_get()
        gs = ob.evaluated_get(dg).evaluated_geometry()
        pc = gs.pointcloud
        slip = [d.vector.z for d in pc.attributes["slip"].data]
        ub = [d.vector.z for d in pc.attributes["ub"].data]
        dbg = {k: round(pc.attributes[k].data[0].value, 6) for k in ("c_dbg", "V_dbg", "r_dbg")}
        dbg["uw"] = [round(x, 4) for x in pc.attributes["uw_dbg"].data[0].vector]
        dbg["P"] = [round(x, 3) for x in pc.attributes["position"].data[0].vector]
        res = {"dbg": dbg, "ms_per_frame": round(ms, 1), "slip_z_mean": round(sum(slip) / len(slip), 4),
               "ub_z_mean": round(sum(ub) / len(ub), 4)}
        if label == "stream":
            water = probe(ob, "vel", (-0.05, -0.05, 0.3), (0.05, 0.05, 0.6), n=6)
            res["water_up_in_column"] = round(sum(v[2] for v in water) / len(water), 4)
        out[label] = res
    out["slip_theory"] = round(u_th, 4)
    return out


def pfm_plume_debug():
    out = {}
    for affine in (False, True):
        out["affine" if affine else "plain"] = _pfm_plume(affine)
    return out


def _pfm_plume(affine):
    sc = fresh()
    ob = obj("pfm")
    src = recipes.get("pfm_smoke").script
    attach(ob, src, {"voxel_size": 0.05, "max": (1, 1, 3), "affine_gradient": 1.0 if affine else 0.0})
    out = {}
    sc.frame_set(1)
    for f in range(2, 31):
        sc.frame_set(f)
        if f >= 14 and f <= 26:
            vel = probe_column(ob, "vel", True)
            out[f"vzmax_{f}"] = round(max(v[2] for v in vel), 3)
        if f in (5, 15, 30):
            d = probe(ob, "density", (-0.02, -0.02, 0.1), (0.02, 0.02, 2.9), n=1, vector=False) if False else None
            col = probe_column(ob, "density", False)
            vel = probe_column(ob, "vel", True)
            out[f] = {"density_top": max([i for i, x in enumerate(col) if x > 0.05] or [0]) * 0.1,
                      "dmax": round(max(col), 3), "vz": [round(v[2], 2) for v in vel][::3]}
    return out


def probe_column(target, grid, vector):
    src = (f'v@s = volumesamplev(chobj("src"), "{grid}", @P);' if vector else
           f'f@s = volumesample(chobj("src"), "{grid}", @P);')
    p = obj("col_" + grid)
    p.data.clear_geometry()
    p.data.from_pydata([(0.01, 0.01, 0.05 + 0.1 * i) for i in range(29)], [], [])
    attach(p, src, {"src": target})
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    gs = p.evaluated_get(dg).evaluated_geometry()
    data = gs.mesh.attributes["s"].data
    vals = [tuple(d.vector) for d in data] if vector else [d.value for d in data]
    del data, gs
    bpy.data.objects.remove(p)
    return vals


def affine_exact():
    """Affine particle-to-grid of a linear field m = A·x with ∇m = A must be exact at voxel centres."""
    src = """
volumebounds({-1, -1, -1}, {1, 1, 1}, 0.1);
grid(1.6, 1.6, 60, 60);
@P.z = sin(@P.x * 3) * 0.3;
topoints();
matrix A = matrix3({0.5, 2, 0}, {-1, 0.3, 0}, {0, 0.7, 1});
vector m = mulvec(A, @P);
rasterize("mS", m - mulvec(A, @P), 0, "linear");
rasterize("G0", matcol(A, 0), 0, "linear");
rasterize("G1", matcol(A, 1), 0, "linear");
rasterize("G2", matcol(A, 2), 0, "linear");
rasterize("W", 1, 0, "linear");
rasterize("plain", m, 0, "linear", "average");
runover(voxel) {
    float wsum = max(f@W, 1e-6);
    v@aff = (v@mS + v@G0 * @P.x + v@G1 * @P.y + v@G2 * @P.z) / wsum;
    v@exact = mulvec(A, @P);
    f@err_aff = f@W > 0.5 ? length(v@aff - v@exact) : 0;
    f@err_plain = f@W > 0.5 ? length(v@plain - v@exact) : 0;
    f@covered = f@W > 0.5 ? 1 : 0;
}
f@ea = volumesample(0, "err_aff", @P, "nearest");
f@ep = volumesample(0, "err_plain", @P, "nearest");
"""
    fresh()
    ob = obj("aff")
    attach(ob, src)
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    gs = ob.evaluated_get(dg).evaluated_geometry()
    pc = gs.pointcloud
    ea = [d.value for d in pc.attributes["ea"].data]
    ep = [d.value for d in pc.attributes["ep"].data]
    return {"affine_err_max": round(max(ea), 5), "affine_err_mean": round(sum(ea) / len(ea), 5),
            "plain_err_mean": round(sum(ep) / len(ep), 5)}


def pfm_ring_debug():
    src = recipes.get("pfm_smoke").script.replace('keep("volume");', '')
    params = {"min": (-0.9, -0.9, 0.0), "max": (0.9, 0.9, 3.2), "voxel_size": 0.04, "buoyancy": 0.0,
              "radius": 0.0001, "per_voxel": 1, "reinit_frames": 3, "affine_gradient": 1.0, "rings": 2,
              "ring_speed": 4.0, "spacing": 0.32, "ring_center": (0, 0, 0.3), "ring_radius": 0.3, "smoke_core": 0.06}
    sc = fresh()
    ob = obj("ring")
    attach(ob, src, params)
    out = {}
    sc.frame_set(1)
    for f in range(1, 31):
        sc.frame_set(f)
        if f in (1, 2, 3, 4, 10, 30):
            dg = bpy.context.evaluated_depsgraph_get()
            gs = ob.evaluated_get(dg).evaluated_geometry()
            pc = gs.pointcloud
            d = [x.value for x in pc.attributes["density"].data]
            z = [x.vector.z for x, dd in zip(pc.attributes["position"].data, d) if dd > 0.3]
            out[f] = {"n": len(d), "dmax": round(max(d), 3), "n_smoke": sum(1 for x in d if x > 0.3),
                      "z_mean": round(sum(z) / len(z), 3) if z else None}
            del pc, gs
    return out


def pfm_ring_dump():
    """Dump smoke-particle positions over time (for plotting the leapfrog)."""
    src = recipes.get("pfm_smoke").script.replace('keep("volume");', '')
    params = {"min": (-0.9, -0.9, 0.0), "max": (0.9, 0.9, 3.2), "voxel_size": VOX, "buoyancy": 0.0,
              "radius": 0.0001, "per_voxel": 2, "reinit_frames": REINIT, "affine_gradient": AFFINE, "rings": 2,
              "ring_speed": SPEED, "spacing": 0.32, "ring_center": (0, 0, 0.3), "ring_radius": 0.3,
              "smoke_core": 0.13, "substeps": SUB, "max_stretch": STRETCH}
    sc = fresh()
    ob = obj("ring")
    attach(ob, src, params)
    out = {}
    for f in range(1, FRAMES + 1):
        sc.frame_set(f)
        if f in (1, 5, 10, 20, 30, 45, 60, 90):
            dg = bpy.context.evaluated_depsgraph_get()
            gs = ob.evaluated_get(dg).evaluated_geometry()
            pc = gs.pointcloud
            d = [x.value for x in pc.attributes["density"].data]
            P = [tuple(round(c, 3) for c in x.vector) for x in pc.attributes["position"].data]
            sm = [(p[0], p[1], p[2], round(dd, 2)) for p, dd in zip(P, d) if dd > 0.05]
            out[f] = {"n": len(d), "n_smoke": len(sm), "pts": sm[::max(1, len(sm) // 4000)]}
            del pc, gs
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", f"ring_dump_{TAG}.json")
    json.dump(out, open(path, "w"))
    return {"file": path, "counts": {k: v["n_smoke"] for k, v in out.items()}}


SMOKE_GRID = """
volumebounds({-1, -1, 0}, {1, 1, 2.5}, chf("h", 0.04));
simulate {
    runover(voxel) {
        vector p = @P - {0, 0, 0.4};
        float rr = length(set(p.x, p.y, 0));
        float disc = smoothstep(0.35, 0.3, rr) * smoothstep(0.08, 0, abs(p.z));
        if (@Frame <= 1) {
            v@vel = {0, 0, 1} * disc;
            f@density = smoothstep(0.09, 0, length(set(rr - 0.35, p.z, 0)));
        }
    }
    volumeproject("vel");
    volumeadvect("density", "vel", deltatime, "maccormack");
    volumeadvect("vel", "vel", deltatime);
}
"""


def smoke_sharpness():
    """Same vortex ring + smoke: MacCormack grid advection of the density vs. density carried on the
    flow-map particles. Reports the peak density and the volume of dense smoke (> 0.5) kept."""
    out = {}
    lo, hi = (-0.7, -0.7, 0.1), (0.7, 0.7, 1.9)
    for label in ("grid_maccormack", "particle_flow_map"):
        sc = fresh()
        ob = obj(label)
        if label == "grid_maccormack":
            attach(ob, SMOKE_GRID)
        else:
            src = recipes.get("pfm_smoke").script
            attach(ob, src, {"buoyancy": 0.0, "radius": 0.0001, "ring_speed": 1.0, "max": (1, 1, 2.5),
                             "voxel_size": 0.04, "reinit_frames": REINIT, "per_voxel": 2, "rings": 1,
                             "ring_center": (0, 0, 0.4), "ring_radius": 0.35, "smoke_core": 0.09})
        rows = {}
        for f in range(1, 61):
            sc.frame_set(f)
            if f in (2, 30, 60):
                d = probe(ob, "density", lo, hi, n=40, vector=False)
                rows[f] = {"max": round(max(d), 3), "dense": sum(1 for x in d if x > 0.5)}
        out[label] = {**rows, "dense_kept": round(rows[60]["dense"] / max(rows[2]["dense"], 1), 3),
                      "peak_kept": round(rows[60]["max"] / max(rows[2]["max"], 1e-6), 3)}
    return out


VOX, REINIT, AFFINE, FRAMES, TAG, SPEED, SUB, STRETCH = 0.03, 3, 1.0, 60, "a", 4.0, 3, 0.6
TESTS = {"smoke_sharpness": smoke_sharpness, "pfm_ring_dump": pfm_ring_dump, "pfm_ring_debug": pfm_ring_debug, "affine_exact": affine_exact, "pfm_plume_debug": pfm_plume_debug, "pfm_vs_grid": pfm_vs_grid, "pfm_sweep": pfm_sweep, "bubbles_terminal": bubbles_terminal}


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else list(TESTS)
    argv = [a for a in argv if a != "--"]
    for a in [a for a in argv if "=" in a]:  # KEY=value overrides module globals
        k, v = a.split("=", 1)
        globals()[k] = type(globals()[k])(v)
    for name in [a for a in argv if "=" not in a]:
        t = time.time()
        try:
            r = TESTS[name]()
            print("RESULT", json.dumps({"test": name, "seconds": round(time.time() - t, 1), **r}), flush=True)
        except Exception as e:  # report and continue
            import traceback
            traceback.print_exc()
            print("RESULT", json.dumps({"test": name, "error": str(e)}), flush=True)
