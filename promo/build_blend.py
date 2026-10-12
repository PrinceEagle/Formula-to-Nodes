# SPDX-License-Identifier: GPL-3.0-or-later
"""Builds the two paper scenes into one .blend (Blender 5.3):
    blender -b --factory-startup -P promo/build_blend.py -- /path/to/Formula_to_Nodes_Papers.blend"""
import os, sys, types
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(HERE), HERE]
import bpy
import sims

out = sys.argv[sys.argv.index("--") + 1]
bpy.ops.wm.read_factory_settings(use_empty=True)
for name, shot, voxel, density in (("FTN pfm_smoke (SIGGRAPH 2024)", "pfm_smoke", 0.03, 3.0),
                                   ("FTN weta_bubbles (EG 2025)", "weta_bubbles", 0.0, 8.0)):
    sims.LIVE_SCENE = name
    args = types.SimpleNamespace(voxel=voxel, density=density, merge=None)
    sc, ob, res, src = sims.SHOTS[shot](args)
    sc.frame_start, sc.frame_end = 1, 120
    sc.render.resolution_x, sc.render.resolution_y = 1080, 1350
    sc.cycles.samples = 64
    print("BUILT", name, res.node_count, flush=True)
if "Scene" in bpy.data.scenes and len(bpy.data.scenes) > 1:
    bpy.data.scenes.remove(bpy.data.scenes["Scene"])
bpy.context.window.scene = bpy.data.scenes[0] if bpy.context.window else None
bpy.ops.wm.save_as_mainfile(filepath=out)
print("SAVED", out)
