# SPDX-License-Identifier: GPL-3.0-or-later
"""Recipes: ready-made scripts, Houdini-wrangle style, grouped by what they
are for. Every recipe is compiled and evaluated by the test suite, shown in
the Recipes menu, offered to MCP clients, and shipped as node-group assets.

Recipe fields
    key, title, category, description, script
    tags     search words
    needs    what to plug in (shown in the UI)
    test     how the test suite runs it: {"kind": "ico"|"grid"|..., "inputs": {...}, "prelude": "..."}
"""

CATEGORIES = ["From Houdini", "Masks", "Deformers", "Scatter & Instance", "Growth & Simulation", "Smoke & Fire", "Liquids",
              "Volumes", "Effectors", "Color", "Curves", "Modeling", "Utility"]
VOLUME_CATEGORIES = ("Smoke & Fire", "Volumes")     # recipes built on grids


class Recipe:
    __slots__ = ("key", "title", "category", "description", "script", "tags", "needs", "test", "blender")

    def __init__(self, key, title, category, description, script, tags=(), needs=(), test=None, blender=(5, 2, 0)):
        self.key, self.title, self.category, self.description = key, title, category, description
        self.script, self.tags, self.needs, self.test = script.strip() + "\n", tuple(tags), tuple(needs), test or {}
        self.blender = tuple(blender)    # oldest Blender the recipe builds on

    def as_dict(self):
        return {"key": self.key, "title": self.title, "category": self.category,
                "description": self.description, "tags": list(self.tags), "needs": list(self.needs),
                "blender": ".".join(str(x) for x in self.blender), "script": self.script}


R = Recipe
RECIPES = [
    # ── From Houdini: the SOPs Houdini artists reach for, by their Houdini names ──────────
    R("attrib_transfer", "Attribute Transfer", "From Houdini",
      "Houdini's Attribute Transfer: copies colour (Cd) from the nearest surface of the 'Input 1' object, "
      "fading out over a blend distance. Also stores the weight in f@transfer_weight.",
      """
// Houdini: Attribute Transfer. Pick the source object as "Input 1" on the modifier.
float d = xyzdist(1, @P);
float reach = chf("Blend/distance", 0.1, min=0);
float w = 1 - smoothstep(reach, reach + chf("Blend/falloff", 0.3, min=0.001), d);
vector base = haspointattrib(0, "Cd") ? v@Cd : chv("base_color", {0.5, 0.5, 0.5});
@Cd = lerp(base, primuv(1, "Cd", @P), w);
f@transfer_weight = w;
""", tags=("houdini", "attribute transfer", "attribtransfer", "copy attribute", "nearest", "colour"),
      needs=("Input 1: the object to copy from (with a Cd attribute)",), test={"inputs": {"Input 1": "cube"}}),

    R("group_expression", "Group Expression", "From Houdini",
      "Houdini's Group Expression: b@group marks the faces matching a rule — facing a direction, above a "
      "height, noise, or a random percentage — and can delete them (Blast).",
      """
#runover prim
// Houdini: Group Expression (+ Blast). The group is the bool attribute b@group.
int rule = chi("Rule/mode", 0, min=0, max=3, tip="0 facing a direction · 1 above a height · 2 noise · 3 random");
float score = 0;
if (rule == 0) score = dot(normalize(@N), normalize(chv("Rule/direction", {0, 0, 1})));
else if (rule == 1) score = @P.z - chf("Rule/height", 0);
else if (rule == 2) score = noise(@P * chf("Rule/noise_scale", 2));
else score = rand(@primnum + chi("Rule/seed", 0));
b@group = rule == 3 ? score < chf("Rule/percent", 0.3) : score > chf("Rule/threshold", 0.7);
if (chb("Blast/delete_group", 0) && b@group) removeprim(0, @primnum, 1);
""", tags=("houdini", "group", "group expression", "selection", "blast", "delete", "mask")),

    R("poly_extrude", "PolyExtrude a group", "From Houdini",
      "Houdini's PolyExtrude on a group: faces facing up (or a random share of them) are extruded; "
      "b@extrudeFront and b@extrudeSide mark the new faces like PolyExtrude's front/side groups.",
      """
#runover prim
// Houdini: PolyExtrude. Only faces in the selection extrude; individual = 1 extrudes each face alone.
b@sel = dot(normalize(@N), {0, 0, 1}) > chf("Select/facing_up", 0.7)
        && rand(@primnum + chi("Select/seed", 0)) < chf("Select/percent", 1, min=0, max=1);
if (b@sel) extrude(chf("distance", 0.2), chi("individual", 0, min=0, max=1));
""", tags=("houdini", "polyextrude", "extrude", "front", "side", "group"), test={"kind": "grid"}),

    R("connectivity", "Connectivity: per-piece colour & offset", "From Houdini",
      "Houdini's Connectivity + For-Each piece: @island numbers connected pieces (stored as i@class); "
      "each piece gets a random colour and can shrink or push away from the centre around its own middle.",
      """
// Houdini: Connectivity (i@class) and a for-each-piece transform, without a loop: aggregates per @island.
i@class = @island;
vector center = avgof(@P, @island);
float r = rand(@island + chi("seed", 0));
@Cd = hsvtorgb(set(r, chf("Color/saturation", 0.6), chf("Color/value", 0.9)));
float s = fit01(rand(@island + 7), chf("Pieces/scale_min", 0.8, min=0), 1);
vector out = normalize(center - getbbox_center(0));
@P = center + (@P - center) * s + out * chf("Pieces/explode", 0);
""", tags=("houdini", "connectivity", "class", "pieces", "island", "foreach", "for each", "explode")),

    R("sweep_profile", "Sweep a profile curve", "From Houdini",
      "Houdini's Resample + Sweep: resamples the curves, then sweeps the 'Profile' curve object along them "
      "with a width ramp and a twist. Draw the profile around its origin in the X/Y plane.",
      """
// Houdini: Resample + Sweep (with a profile). @curveparam is Houdini's @curveu.
resample(chi("segments", 64, min=2));
f@tilt = @curveparam * radians(chf("twist_degrees", 0));
float w = chramp("Width/along", @curveparam, "plateau") * chf("Width/scale", 1);
sweep(chobj("Profile"), w);
""", tags=("houdini", "sweep", "resample", "profile", "road", "pipe", "ribbon", "curveu"),
      needs=("curves (convert a mesh with tocurves())", "Profile: a curve object"),
      test={"kind": "line", "prelude": "tocurves();", "inputs": {"Profile": "cube"}}),

    R("mountain", "Mountain", "From Houdini",
      "Houdini's Mountain SOP: fractal noise pushes points along their normals, with height, element size, "
      "octaves, roughness and offset.",
      """
// Houdini: Mountain. Same controls: height, element size, roughness, offset.
float h = fbm(@P / chf("element_size", 1, min=0.001) + chv("offset", {0, 0, 0}), 1,
              chi("octaves", 5, min=1, max=10), chf("roughness", 0.5, min=0, max=1));
@P += normalize(@N) * (h - 0.5) * chf("height", 0.5);
""", tags=("houdini", "mountain", "fractal", "displace", "terrain", "noise", "rock")),

    R("peak", "Peak", "From Houdini",
      "Houdini's Peak: moves points along their normals — inflate (positive) or shrink (negative), "
      "optionally only where a mask attribute is high.",
      """
// Houdini: Peak. Optional mask: an attribute name typed into the script (default: everything moves).
float m = haspointattrib(0, "mask") ? f@mask : 1;
@P += normalize(@N) * chf("distance", 0.05) * m;
""", tags=("houdini", "peak", "inflate", "shrink", "push", "normal")),

    R("measure", "Measure: area & curvature", "From Houdini",
      "Houdini's Measure: f@face_area per face and a curvature estimate per point (positive on bumps, "
      "negative in dents) — ready to drive masks, colour or scattering.",
      """
// Houdini: Measure. f@face_area per face; f@curvature per point (how far a point sticks out of its neighbours).
runover(prim) { f@face_area = @area; }
vector avg = blur(@P, chi("smoothing", 1, min=1));
f@curvature = dot(normalize(@N), @P - avg) / chf("scale", 0.02, min=0.0001);
@Cd = colorramp("view", fit(f@curvature, -1, 1, 0, 1), "heat");
""", tags=("houdini", "measure", "area", "curvature", "convexity", "cavity")),

    R("attrib_noise", "Attribute Noise", "From Houdini",
      "Houdini's Attribute Noise: animated fractal noise into f@noise, remapped by a ramp — and optionally "
      "into colour or pushed along the normals.",
      """
// Houdini: Attribute Noise. Mode: 0 f@noise only · 1 also colour · 2 also displace along N.
vector p = @P * chf("Noise/frequency", 2) + {0, 0, 1} * @Time * chf("Noise/speed", 0.5)
           + chv("Noise/offset", {0, 0, 0});
float n = fit(snoise(p, 1, chi("Noise/octaves", 3, min=1), chf("Noise/roughness", 0.5)), -1, 1, 0, 1);
n = chramp("remap", n, "linear");
f@noise = n;
int mode = chi("Output/mode", 0, min=0, max=2);
if (mode == 1) @Cd = colorramp("colors", n, "viridis");
if (mode == 2) @P += normalize(@N) * (n - 0.5) * chf("Output/amplitude", 0.2);
""", tags=("houdini", "attribute noise", "attribnoise", "noise", "animated", "displace", "colour")),

    R("copy_prep", "Copy to Points prep", "From Houdini",
      "The usual wrangle before Copy to Points: random @pscale, p@orient with +Z along the normal and a "
      "random spin, and i@variant picking a child of the collection.",
      """
// Houdini: the copy-to-points wrangle. @pscale, p@orient (+Z along @N, random spin), i@variant.
@pscale = fit01(pow(rand(@ptnum + chi("seed", 0)), chf("Scale/bias", 1, min=0.1)),
                chf("Scale/min", 0.5), chf("Scale/max", 1.5));
vector4 align = dihedral({0, 0, 1}, normalize(@N));
p@orient = qmultiply(align, quaternion(rand(@ptnum + 3) * radians(chf("spin_degrees", 360)), {0, 0, 1}));
i@variant = int(rand(@ptnum + 9) * chi("variants", 3, min=1));
instance(chcoll("Collection"), i@variant);
""", tags=("houdini", "copy to points", "pscale", "orient", "instance", "variant"),
      needs=("Collection: the objects to copy",), test={"inputs": {"Collection": "collection"}}),

    R("point_relax", "Point Relax", "From Houdini",
      "Houdini's Relax for scattered points: each point steps away from its nearest neighbour until they "
      "are about 'radius' apart — removes clumps for even instancing.",
      """
// Houdini: Relax. A few iterations of pushing away from the nearest other point.
float r = chf("radius", 0.1, min=0.001);
repeat(chi("iterations", 20, min=1, max=200)) {
    int nb = nearpoint(0, @P);
    vector away = @P - point(0, "P", nb);
    float d = length(away);
    // two points on top of each other: split them in a random direction
    vector dir = d > 0.000001 ? away / d : normalize(set(rand(@ptnum + iteration), rand(@ptnum + 7), 0) - 0.5);
    @P += dir * max(r - d, 0) * 0.5 * chf("strength", 1, min=0, max=1);
}
""", tags=("houdini", "relax", "point relax", "even", "spread", "declump", "scatter"),
      test={"kind": "plane", "prelude": "scatter(200);"}),

    R("pop_source", "POP Source + forces", "Growth & Simulation",
      "Houdini's POP Source with POP Force, Drag and Curl: particles are born from the surface over time, "
      "live for a random life, then die. Writes @v, f@age, f@life and @pscale for instancing or rendering.",
      """
// Houdini: POP Source → POP Force / Drag / Curl Force → age & reap. Blender is Z-up.
scatter(chf("Source/pool_density", 300), chi("Source/seed", 0));
simulate {
    init {
        f@birth = rand(@ptnum) * chf("Source/emit_seconds", 3);
        f@life = fit01(rand(@ptnum + 1), chf("Life/min", 1), chf("Life/max", 2));
        f@age = 0;
        @v = @N * chf("Source/speed", 1);
    }
    float alive = @Time >= f@birth ? 1 : 0;
    vector curl = curlnoise(@P + {0, 0, 1} * @Time, chf("Forces/curl_scale", 1)) * chf("Forces/curl", 2);
    @v += (chv("Forces/gravity", {0, 0, -2}) + curl - @v * chf("Forces/drag", 0.5)) * deltatime * alive;
    @P += @v * deltatime * alive;
    f@age += deltatime * alive;
    @pscale = alive * chf("size", 0.02) * (1 - f@age / max(f@life, 0.001));
    if (f@age > f@life) removepoint(0, @ptnum);
}
// show only born particles (the unborn pool stays in the simulation, like POP Source's stream)
if (@Time < f@birth) removepoint(0, @ptnum);
""", tags=("houdini", "pop", "particles", "source", "emit", "age", "life", "curl", "drag", "simulation"),
      test={"kind": "plane", "frames": 3}),

    R("age_color", "Age to colour & size", "Color",
      "The classic POP look-dev wrangle: normalized age (f@age / f@life) through ramps into @Cd and @pscale. "
      "Use after POP Source + forces, or any script that writes f@age and f@life.",
      """
// Houdini POP look-dev: life fraction → colour and size ramps.
float t = clamp(f@age / max(f@life, 0.0001), 0, 1);
@Cd = colorramp("Look/color_over_life", t, "fire");
@pscale = chramp("Look/size_over_life", t, "bell") * chf("Look/size", 0.03);
""", tags=("houdini", "pop", "age", "life", "colour", "ramp", "particles")),

    # ── motion design (MoGraph habits) ──────────────────────────────────────
    R("stagger_in", "Stagger in", "Effectors",
      "Instances animate in one after another — ordered by index, by distance from the 'Origin' object, or "
      "randomly — growing with an overshooting ease and dropping in from an offset.",
      """
#runover instance
// Each instance starts at its own frame; easeoutback gives the overshoot.
int by = chi("Delay/order_by", 0, min=0, max=2, tip="0 index · 1 distance from Origin · 2 random");
float d = length(v@P - objpos(chobj("Delay/Origin")));
float order = by == 0 ? @elemnum / max(@numelem - 1, 1) : by == 1 ? d / max(maxof(d), 0.0001) : rand(@elemnum);
float start = chf("Timing/start_frame", 1) + order * chf("Timing/spread_frames", 48);
float t = clamp((@Frame - start) / max(chf("Timing/duration_frames", 12), 1), 0, 1);
@scale *= easeoutback(t, chf("Timing/overshoot", 1.7));
v@P += chv("from_offset", {0, 0, 1}) * (1 - easeout(t, 3));
""", tags=("mograph", "stagger", "animate in", "delay", "ease", "build", "motion design"),
      needs=("instances (from a cloner or scatter)",),
      test={"prelude": 'points(9);\n@P = set(@ptnum % 3, floor(@ptnum / 3), 0);\ninstance(chobj("I"));',
            "inputs": {"I": "cube"}, "frames": 3}),

    R("random_effector", "Random effector", "Effectors",
      "MoGraph's Random effector: per-instance random position, rotation and scale with a seed, optionally "
      "only inside a spherical falloff around the 'Falloff' object.",
      """
#runover instance
#include "falloff.h"
// Random effector with an optional spherical falloff.
float w = chb("Falloff/use", 0) ? falloff_sphere(v@P, objpos(chobj("Falloff/Object")),
                                                 chf("Falloff/radius", 2), chf("Falloff/softness", 0.5)) : 1;
int s = chi("seed", 0) * 7;
vector r3 = set(rand(@elemnum * 3 + s), rand(@elemnum * 3 + 1 + s), rand(@elemnum * 3 + 2 + s)) * 2 - 1;
v@P += r3 * chv("Random/position", {0.2, 0.2, 0.2}) * w;
vector deg = radians(chv("Random/rotation_degrees", {0, 0, 45})) * r3 * w;
p@orient = qmultiply(p@orient, qmultiply(quaternion(deg.z, {0, 0, 1}),
                     qmultiply(quaternion(deg.y, {0, 1, 0}), quaternion(deg.x, {1, 0, 0}))));
@scale *= 1 + (rand(@elemnum + 99 + s) * 2 - 1) * chf("Random/scale", 0.3) * w;
""", tags=("mograph", "random", "effector", "jitter", "falloff", "motion design"),
      needs=("instances (from a cloner or scatter)",),
      test={"prelude": 'points(9);\n@P = set(@ptnum % 3, floor(@ptnum / 3), 0);\ninstance(chobj("I"));',
            "inputs": {"I": "cube"}}),

    R("step_effector", "Step & cascade", "Effectors",
      "MoGraph's Step effector with a time offset: scale and offset change progressively from the first "
      "instance to the last along a curve, and a wave runs through them with a delay per instance.",
      """
#runover instance
// u: 0 for the first instance, 1 for the last.
float u = @elemnum / max(@numelem - 1, 1);
float s = chramp("Step/curve", u, "linear");
@scale *= lerp(chf("Step/scale_first", 1), chf("Step/scale_last", 0.3), s);
v@P += chv("Step/offset_last", {0, 0, 0}) * s;
float phase = (@Time * chf("Cascade/speed", 1) - u * chf("Cascade/delay", 2)) * 2 * pi;
v@P.z += sin(phase) * chf("Cascade/height", 0.3);
""", tags=("mograph", "step", "cascade", "time offset", "delay", "wave", "motion design"),
      needs=("instances (from a cloner or scatter)",),
      test={"prelude": 'points(9);\n@P = set(@ptnum, 0, 0);\ninstance(chobj("I"));',
            "inputs": {"I": "cube"}, "frames": 3}),

    R("radial_cloner", "Radial cloner", "Scatter & Instance",
      "MoGraph's radial cloner: N copies of the 'Shape' object around a circle or an arc, facing outward.",
      """
// Radial cloner: count copies around an arc of sweep_degrees.
points(chi("count", 12, min=1));
float full = chf("sweep_degrees", 360, min=0, max=360);
float a = radians(chf("start_degrees", 0)) + radians(full) * @ptnum / max(full >= 360 ? @numpt : @numpt - 1, 1);
@P = set(cos(a), sin(a), 0) * chf("radius", 2);
p@orient = quaternion(a * chb("face_outward", 1), {0, 0, 1});
instance(chobj("Shape"));
""", tags=("mograph", "cloner", "radial", "circle", "array", "instance", "motion design"),
      needs=("Shape: the object to clone",), test={"inputs": {"Shape": "cube"}}),

    # ── Masks ────────────────────────────────────────────────────────────────
    R("slope_mask", "Slope mask", "Masks",
      "1 on flat, upward faces and 0 on steep walls — for moss, snow or grass placement.",
      """
// f@slope: 1 = flat ground, 0 = steeper than the 'steep' angle
float steep = degrees(acos(clamp(dot(normalize(v@N), {0, 0, 1}), -1, 1)));
f@slope = 1 - smoothstep(chf("Angle/flat_below", 20), chf("Angle/steep_above", 45), steep);
""", tags=("snow", "moss", "slope", "angle")),

    R("height_mask", "Height mask", "Masks",
      "0 at the bottom of the object, 1 at the top, shaped by a curve you draw in the sidebar.",
      """
f@height = chramp("profile", relbbox(0, v@P).z, "smooth");
""", tags=("gradient", "height", "ramp")),

    R("edge_wear", "Edge wear and cavity", "Masks",
      "Finds convex edges (worn, chipped paint) and cavities (dirt) by comparing each point "
      "with its neighbours.",
      """
// how far each point sticks out of its blurred neighbourhood
vector avg = blur(v@P, chi("radius", 2, min=1));
float bulge = dot(normalize(v@N), v@P - avg);
f@wear = smoothstep(0, chf("sensitivity", 0.02), bulge);
f@cavity = smoothstep(0, chf("sensitivity", 0.02), -bulge);
""", tags=("curvature", "convex", "concave", "dirt", "wear")),

    R("distance_mask", "Distance to object", "Masks",
      "1 close to the surface of another object, fading to 0 at the radius.",
      """
// set "Input 1" on the modifier to the object to measure to
float d = xyzdist(1, v@P);
f@near = 1 - smoothstep(0, chf("radius", 0.5), d);
""", tags=("proximity", "distance", "contact"), needs=("Input 1: an object",),
      test={"inputs": {"Input 1": "cube"}}),

    R("noise_mask", "Noise mask", "Masks",
      "Organic patches from fractal noise with a threshold and soft edge.",
      """
float n = fbm(v@P, chf("scale", 3), chf("detail", 5), 0.55);
float t = chf("threshold", 0.5);
float soft = chf("softness", 0.05);
f@mask = smoothstep(t - soft, t + soft, n);
""", tags=("noise", "patches", "breakup")),

    R("ao_mask", "Ambient occlusion (ray traced)", "Masks",
      "Casts 8 rays around each normal and counts how many hit the mesh: 1 in crevices, 0 in the open.",
      """
vector dirs[] = {{0, 0, 1}, {0.6, 0, 0.8}, {-0.6, 0, 0.8}, {0, 0.6, 0.8}, {0, -0.6, 0.8},
                 {0.42, 0.42, 0.8}, {-0.42, 0.42, 0.8}, {0.42, -0.42, 0.8}};
vector4 frame = dihedral({0, 0, 1}, v@N);
vector origin = v@P + v@N * 0.001;
float dist = chf("distance", 1);
float hits = 0;
foreach (vector d; dirs) {
    hits += rayhit(0, origin, qrotate(frame, d) * dist);
}
f@occlusion = hits / len(dirs);
""", tags=("ao", "occlusion", "raycast", "crevice")),

    R("moss", "Moss on top", "Masks",
      "Moss where faces point up, broken up with noise.",
      """
float up = max(dot(normalize(v@N), {0, 0, 1}), 0);
float n = noise(v@P, chf("noise_scale", 4));
f@moss = smoothstep(chf("coverage", 0.5), 1, pow(up, chf("sharpness", 2)) + (n - 0.5) * chf("breakup", 0.6));
""", tags=("moss", "snow", "dust")),

    # ── Deformers ────────────────────────────────────────────────────────────
    R("bend", "Bend", "Deformers",
      "Bends the object around the Z axis along X (like Simple Deform › Bend).",
      """
float len = max(getbbox_size(0).x, 0.0001);
float k = radians(chf("angle", 90)) / len;
if (abs(k) > 0.00001) {
    float r = 1 / k;
    float a = @P.x * k;
    @P = set(sin(a) * (r - @P.y), r - cos(a) * (r - @P.y), @P.z);
}
""", tags=("bend", "deform", "arc")),

    R("twist", "Twist", "Deformers",
      "Twists around an axis, more toward the top; the falloff curve shapes where it happens.",
      """
float zmin = getbbox_min(0).z;
float h = max(getbbox_size(0).z, 0.0001);
float t = (@P.z - zmin) / h;
float angle = radians(chf("angle", 180)) * chramp("falloff", t, "linear");
@P = rotate(v@P, chv("axis", 0, 0, 1), angle);
""", tags=("twist", "deform")),

    R("taper", "Taper", "Deformers",
      "Scales the object's width from bottom to top.",
      """
vector c = getbbox_center(0);
float t = relbbox(0, v@P).z;
float s = lerp(chf("bottom", 1), chf("top", 0.3), chramp("profile", t, "linear"));
@P = set(c.x + (@P.x - c.x) * s, c.y + (@P.y - c.y) * s, @P.z);
""", tags=("taper", "deform", "scale")),

    R("spherize", "Spherize", "Deformers",
      "Blends points toward a sphere around the object's centre.",
      """
vector c = getbbox_center(0);
vector onsphere = c + normalize(v@P - c) * chf("radius", 1);
@P = lerp(v@P, onsphere, chf("amount", 1));
""", tags=("sphere", "inflate", "deform")),

    R("ripple", "Ripple", "Deformers",
      "Animated waves rolling out from the 'Input 1' object (or the origin).",
      """
vector c = objpos(1);
float d = distance(v@P, c);
float wave = sin(d * chf("frequency", 8) - @Time * chf("speed", 4));
float fade = 1 - smoothstep(0, chf("radius", 3), d);
@P += v@N * wave * chf("amplitude", 0.05) * fade;
""", tags=("wave", "ripple", "animated"), needs=("Input 1: an object marking the centre (optional)",)),

    R("curl_flow", "Curl noise flow", "Deformers",
      "Smoke-like swirling displacement from divergence-free curl noise.",
      """
vector p = v@P + {0, 0, 1} * @Time * chf("evolve", 0.2);
@P += curlnoise(p, chf("scale", 1.5)) * chf("strength", 0.2);
""", tags=("curl", "swirl", "flow", "noise")),

    R("melt", "Melt", "Deformers",
      "Sags the object down onto its base over time, spreading outward.",
      """
float floor_z = getbbox_min(0).z;
vector c = getbbox_center(0);
float h = relbbox(0, v@P).z;
float t = clamp(@Frame / chf("frames", 120), 0, 1);
float sag = t * chf("amount", 1) * h * (0.6 + noise(v@P, 3) * 0.8);
@P.x += (@P.x - c.x) * sag * chf("spread", 0.3);
@P.y += (@P.y - c.y) * sag * chf("spread", 0.3);
@P.z = max(@P.z - sag, floor_z);
""", tags=("melt", "sag", "animated")),

    R("smooth", "Smooth (Laplacian)", "Deformers",
      "Relaxes the surface by averaging each point with its neighbours.",
      """
@P = lerp(v@P, blur(v@P, chi("iterations", 5, min=0)), chf("strength", 1));
""", tags=("smooth", "relax", "blur")),

    R("shrinkwrap", "Shrinkwrap", "Deformers",
      "Pulls points onto the closest surface of the 'Input 1' object.",
      """
vector target = minpos(1, v@P);
@P = lerp(v@P, target, chf("amount", 1)) + v@N * chf("offset", 0);
""", tags=("shrinkwrap", "project", "snap"), needs=("Input 1: the target object",),
      test={"inputs": {"Input 1": "cube"}}),

    R("noise_displace", "Noise displacement", "Deformers",
      "Pushes points along their normals with signed noise and stores the amount.",
      """
float n = snoise(v@P, chf("scale", 2.0));
@P += v@N * n * chf("strength", 0.3);
f@displacement = n;
""", tags=("noise", "displace")),

    R("jitter", "Jitter", "Deformers",
      "Random offset per point (reproducible with the seed).",
      """
vector r = rand(@ptnum + chi("seed", 0));
@P += (r - {0.5, 0.5, 0.5}) * chf("amount", 0.05) * 2;
""", tags=("random", "jitter", "noise")),

    # ── Scatter & Instance ───────────────────────────────────────────────────
    R("scatter_instance", "Scatter & instance a collection", "Scatter & Instance",
      "Scatters points on the surface and copies random children of a collection onto them, "
      "aligned to the surface with random spin and size.",
      """
scatter(chf("density", 30), chi("seed", 0), chf("min_distance", 0.05));
@pscale = fit01(rand(@id + 11), chf("Scale/min", 0.6), chf("Scale/max", 1.2));
p@orient = qmultiply(p@orient, quaternion(rand(@id) * 2 * pi, {0, 0, 1}));
instance(chcoll("Collection"), int(rand(@id + 5) * 1000));
""", tags=("scatter", "instance", "copy to points", "rocks", "trees"),
      needs=("Collection: the objects to copy",), test={"inputs": {"Collection": "collection"}}),

    R("grass", "Grass on flat ground", "Scatter & Instance",
      "Scatters blades only where the surface is flat enough, with noise-driven size and random lean.",
      """
if (dot(normalize(v@N), {0, 0, 1}) > chf("flatness", 0.8)) scatter(chf("density", 200), chi("seed", 1));
@pscale = fit01(noise(v@P, 2) * 0.5 + rand(@id) * 0.5, chf("Scale/min", 0.5), chf("Scale/max", 1.3));
p@orient = qmultiply(p@orient, quaternion(fit01(rand(@id + 3), -0.3, 0.3), {1, 0, 0}));
p@orient = qmultiply(p@orient, quaternion(rand(@id + 7) * 2 * pi, {0, 0, 1}));
instance(chobj("Blade"));
""", tags=("grass", "scatter", "instance", "slope"), needs=("Blade: an object to copy",),
      test={"inputs": {"Blade": "cube"}, "kind": "grid"}),

    R("cloner_grid", "Grid cloner", "Scatter & Instance",
      "A MoGraph-style grid of copies of an object.",
      """
int nx = chi("Grid/count_x", 5, min=1);
int ny = chi("Grid/count_y", 5, min=1);
float sp = chf("Grid/spacing", 1);
points(nx * ny);
@P = set((@ptnum % nx - (nx - 1) * 0.5) * sp, (floor(@ptnum / nx) - (ny - 1) * 0.5) * sp, 0);
instance(chobj("Instance"));
""", tags=("cloner", "mograph", "grid", "array"), needs=("Instance: the object to copy",),
      test={"inputs": {"Instance": "cube"}}),

    R("instance_vertices", "Instance on vertices", "Scatter & Instance",
      "Copies an object onto every vertex, pointing along the normal.",
      """
@pscale = chf("size", 0.2);
p@orient = dihedral({0, 0, 1}, v@N);
instance(chobj("Instance"));
""", tags=("instance", "copy to points", "vertices"), needs=("Instance: the object to copy",),
      test={"inputs": {"Instance": "cube"}}),

    R("along_curve", "Copies along a curve", "Scatter & Instance",
      "Resamples curves and places oriented copies along them, sized by a ramp.",
      """
resample(chi("count", 20, min=2));
p@orient = alignaxis(v@tangent, {0, 0, 1}, "x");
@pscale = chramp("scale", @curveparam, "linear") * chf("size", 1);
instance(chobj("Instance"));
""", tags=("curve", "path", "instance"), needs=("a Curves object", "Instance: the object to copy"),
      test={"kind": "line", "prelude": "tocurves();", "inputs": {"Instance": "cube"}}),

    R("thin_out", "Thin out points", "Scatter & Instance",
      "Removes a random fraction of the points (keeps the same ones when the seed is fixed).",
      """
if (rand(@id + chi("seed", 0)) < chf("remove_fraction", 0.5)) removepoint(0, @ptnum);
""", tags=("random", "delete", "cull")),

    # ── Growth & Simulation ──────────────────────────────────────────────────
    R("particles", "Particles with gravity", "Growth & Simulation",
      "Turns the vertices into particles that launch along the normals, fall and bounce on the ground.",
      """
topoints();
simulate {
    init {
        @v = v@N * chf("launch", 3) + set(snoise(v@P, 4), snoise(v@P + 5, 4), 0) * chf("spread", 1);
    }
    @v += set(0, 0, -chf("gravity", 9.8)) * deltatime;
    @v *= 1 - chf("drag", 0.1) * deltatime;
    @P += @v * deltatime;
    if (@P.z < chf("ground", 0)) {
        @P.z = chf("ground", 0);
        @v = set(@v.x, @v.y, -@v.z * chf("bounce", 0.5));
    }
}
""", tags=("particles", "gravity", "bounce", "simulation")),

    R("reaction_diffusion", "Reaction–diffusion", "Growth & Simulation",
      "Gray–Scott patterns (spots, stripes, coral) growing over a dense mesh.",
      """
simulate {
    init {
        f@a = 1;
        f@b = rand(@ptnum + chi("seed", 3)) < chf("seed_amount", 0.03) ? 1 : 0;
    }
    repeat(chi("steps_per_frame", 8, min=1)) {
        float a = f@a;
        float b = f@b;
        float la = (blur(a, 1) - a) * 4;
        float lb = (blur(b, 1) - b) * 4;
        float abb = a * b * b;
        float feed = chf("feed", 0.055);
        float kill = chf("kill", 0.062);
        f@a = clamp(a + chf("diffuse_a", 1.0) * la - abb + feed * (1 - a), 0, 1);
        f@b = clamp(b + chf("diffuse_b", 0.5) * lb + abb - (kill + feed) * b, 0, 1);
    }
}
@Cd = colorramp("colors", f@b * 3, "magma");
""", tags=("reaction diffusion", "gray scott", "pattern", "simulation"), needs=("a dense mesh",)),

    R("curl_trails", "Curl noise advection", "Growth & Simulation",
      "Points drift through a swirling curl-noise field, frame by frame.",
      """
topoints();
simulate {
    @P += curlnoise(v@P, chf("scale", 1), 0) * chf("speed", 0.5) * deltatime;
}
""", tags=("curl", "advection", "smoke", "simulation")),

    R("grow", "Grow over time", "Growth & Simulation",
      "Inflates the surface frame after frame.",
      """
simulate {
    @P += v@N * deltatime * chf("speed", 0.5);
}
""", tags=("grow", "inflate", "simulation")),

    R("flocking", "Flocking (boids)", "Growth & Simulation",
      "Each point steers away from its nearest neighbour, matches the average heading and "
      "drifts toward the centre of the flock.",
      """
topoints();
simulate {
    init {
        v@vel = set(snoise(v@P, 2), snoise(v@P + 3, 2), snoise(v@P + 7, 2));
    }
    int nb = nearpoint(0, v@P);
    vector away = v@P - point(0, "P", nb);
    float d = max(length(away), 0.0001);
    vector sep = away / d * max(chf("Flock/separation_radius", 0.3) - d, 0) * chf("Flock/separation", 4);
    vector ali = (avgof(v@vel) - v@vel) * chf("Flock/alignment", 0.5);
    vector coh = (avgof(v@P) - v@P) * chf("Flock/cohesion", 0.3);
    v@vel += (sep + ali + coh) * deltatime;
    float sp = clamp(length(v@vel), chf("min_speed", 0.5), chf("max_speed", 2));
    v@vel = normalize(v@vel) * sp;
    @P += v@vel * deltatime;
}
""", tags=("boids", "flock", "swarm", "simulation")),

    R("wave_pool", "Wave pool", "Liquids",
      "Raindrop ripples on a grid that spread and fade.",
      """
simulate {
    init {
        f@h = rand(@ptnum + chi("seed", 1)) < chf("drops", 0.002) ? 1 : 0;
        f@vel = 0;
    }
    repeat(chi("steps", 4, min=1)) {
        float lap = blur(f@h, 1) - f@h;
        f@vel = (f@vel + lap * chf("speed", 2)) * (1 - chf("damping", 0.01));
        f@h += f@vel;
    }
}
@P.z += f@h * chf("height", 0.2);
""", tags=("water", "waves", "ripples", "simulation"), needs=("a dense grid",), test={"kind": "grid"}),

    # ── Effectors ────────────────────────────────────────────────────────────
    R("effector_scale", "Sphere effector: scale instances", "Effectors",
      "Instances near the 'Effector' object (an empty works) shrink or grow — like a MoGraph "
      "plain effector with a spherical falloff. Put it after a cloner or scatter modifier.",
      """
#runover instance
#include "falloff.h"
float w = falloff_sphere(v@P, objpos(chobj("Effector")), chf("radius", 2), chf("softness", 0.5));
@scale *= lerp(1, chf("scale", 0), w);
""", tags=("effector", "falloff", "mograph", "scale"), needs=("instances (from a cloner or scatter)",
                                                             "Effector: an object or empty"),
      test={"prelude": 'points(9);\n@P = set(@ptnum % 3, floor(@ptnum / 3), 0);\ninstance(chobj("I"));',
            "inputs": {"Effector": "cube", "I": "cube"}, "split": True}),

    R("effector_push", "Plane effector: push", "Effectors",
      "Points in front of the 'Effector' object's plane get pushed, with a soft edge.",
      """
#include "falloff.h"
vector origin = objpos(chobj("Effector"));
vector n = qrotate(objrot(chobj("Effector")), {0, 0, 1});
float w = falloff_plane(v@P, origin, n, chf("width", 1));
@P += chv("push", 0, 0, 0.5) * w;
f@weight = w;
""", tags=("effector", "falloff", "plane", "push"), needs=("Effector: an object or empty",),
      test={"inputs": {"Effector": "cube"}}),

    R("effector_noise_rotate", "Noise effector: rotate instances", "Effectors",
      "Rotates each instance by an evolving noise value.",
      """
#runover instance
float n = snoise(v@P + {0, 0, 1} * @Time * chf("evolve", 0.3), chf("scale", 1));
p@orient = qmultiply(quaternion(n * radians(chf("max_angle", 45)), normalize(chv("axis", 0, 0, 1))), p@orient);
""", tags=("effector", "noise", "rotate", "mograph"), needs=("instances (from a cloner or scatter)",),
      test={"prelude": 'points(4);\n@P = set(@ptnum, 0, 0);\ninstance(chobj("I"));', "inputs": {"I": "cube"},
            "split": True}),

    R("effector_color", "Sphere effector: colour", "Effectors",
      "Colours geometry by its distance to the 'Effector' object.",
      """
#include "falloff.h"
float w = falloff_sphere(v@P, objpos(chobj("Effector")), chf("radius", 2), chf("softness", 0.6));
@Cd = lerp(chv("Colors/outside", 0.1, 0.1, 0.1), chv("Colors/inside", 1, 0.3, 0.05), w);
f@weight = w;
""", tags=("effector", "falloff", "color"), needs=("Effector: an object or empty",),
      test={"inputs": {"Effector": "cube"}}),

    # ── Color ────────────────────────────────────────────────────────────────
    R("island_colors", "Random colour per island", "Color",
      "Each separate piece of the mesh gets its own colour.",
      """
#include "color.h"
@Cd = randcolor(@island + chi("seed", 0), chf("saturation", 0.6), chf("value", 0.9));
""", tags=("random", "color", "islands", "pieces")),

    R("height_colors", "Height colour ramp", "Color",
      "Colours by height through a colour ramp (terrain preset).",
      """
@Cd = colorramp("palette", relbbox(0, v@P).z, "terrain");
""", tags=("gradient", "terrain", "color", "ramp")),

    R("normal_colors", "Normals as colours", "Color",
      "Shows normals as RGB — handy to debug orientation.",
      """
@Cd = v@N * 0.5 + {0.5, 0.5, 0.5};
""", tags=("debug", "normals", "color")),

    R("curvature_colors", "Curvature colours", "Color",
      "Convex areas warm, concave areas cool.",
      """
vector avg = blur(v@P, 2);
float c = dot(normalize(v@N), v@P - avg) * chf("contrast", 20);
@Cd = colorramp("curvature", fit(c, -1, 1, 0, 1), "heat");
""", tags=("curvature", "color", "debug")),

    R("voronoi_colors", "Voronoi cells", "Color",
      "Random colour per Voronoi cell, per face.",
      """
#runover prim
@Cd = hsvtorgb(set(cellrand(v@P, chf("cells", 3)), 0.55, 0.9));
""", tags=("voronoi", "cells", "color")),

    # ── Curves ───────────────────────────────────────────────────────────────
    R("tube", "Tapered tube", "Curves",
      "Turns curves into tubes whose radius follows a ramp along each curve.",
      """
f@width = chramp("profile", @curveparam, "ease_out") * chf("radius", 0.1);
sweep(f@width, chi("resolution", 12, min=3));
""", tags=("curve", "tube", "sweep", "taper"), needs=("a Curves object",),
      test={"kind": "line", "prelude": "tocurves();"}),

    R("wiggle", "Wiggle curves", "Curves",
      "Resamples curves and wiggles them with noise, strongest in the middle.",
      """
resample(chi("count", 64, min=2));
@P += (vnoise(v@P, chf("scale", 2)) - {0.5, 0.5, 0.5}) * chf("amount", 0.3) * chramp("along", @curveparam, "bell");
""", tags=("curve", "noise", "wiggle"), needs=("a Curves object",),
      test={"kind": "line", "prelude": "tocurves();"}),

    R("wireframe", "Wireframe tubes", "Curves",
      "Every mesh edge becomes a round tube.",
      """
tocurves();
sweep(chf("thickness", 0.02), chi("resolution", 6, min=3));
""", tags=("wireframe", "edges", "tubes")),

    # ── Modeling ─────────────────────────────────────────────────────────────
    R("greebles", "Greebles", "Modeling",
      "Extrudes a random selection of faces by random heights (sci-fi panels).",
      """
#runover prim
if (rand(@primnum + chi("seed", 0)) < chf("amount", 0.3)) {
    extrude(fit01(rand(@primnum + 17), chf("Height/min", 0.05), chf("Height/max", 0.3)), 1);
}
""", tags=("greeble", "extrude", "panels", "scifi")),

    R("bevel_sharp", "Bevel sharp edges", "Modeling",
      "Bevels only the edges sharper than an angle.",
      """
#runover edge
if (degrees(@edgeangle) > chf("min_angle", 30)) bevel(chf("width", 0.05), chi("segments", 2, min=1));
""", tags=("bevel", "edges", "chamfer"), test={"kind": "cube"}),

    R("hex_pattern", "Hexagon pattern", "Modeling",
      "Subdivides, triangulates and takes the dual mesh: hexagon tiling.",
      """
subdivide(chi("levels", 1, min=0));
triangulate();
dualmesh();
""", tags=("hexagon", "dual", "pattern")),

    R("delete_faces", "Delete random faces", "Modeling",
      "Removes a random fraction of the faces.",
      """
#runover prim
if (rand(@primnum + chi("seed", 0)) < chf("fraction", 0.3)) removeprim(0, @primnum, 1);
""", tags=("delete", "random", "faces")),

    R("explode", "Explode pieces", "Modeling",
      "Pushes every separate piece away from the centre.",
      """
vector piece = avgof(v@P, @island);
@P += normalize(piece - getbbox_center(0)) * chf("distance", 0.5);
""", tags=("explode", "islands", "pieces")),

    # ── Utility ──────────────────────────────────────────────────────────────
    R("audio_pulse", "Audio reactive pulse", "Utility",
      "Pushes the surface out with the loudness of a frequency band of a sound.",
      """
float loud = spectrum(chsound("Music"), chf("Band/low_hz", 40), chf("Band/high_hz", 200)) * chf("gain", 4);
@P += v@N * loud * chf("amount", 0.2) * (0.5 + noise(v@P, 3));
f@loudness = loud;
""", tags=("audio", "sound", "music", "reactive"), needs=("Music: a sound",)),

    R("stats", "Print statistics", "Utility",
      "Shows the point count and size of the geometry on the node and the modifier.",
      """
#runover detail
printf("%d points, %d faces, size %g", npoints(0), nprimitives(0), getbbox_size(0));
""", tags=("debug", "info", "printf")),

    R("surface_area", "Measure surface area", "Utility",
      "Adds up the face areas into a detail attribute and shows the total.",
      """
#runover prim
setdetailattrib(0, "area", @area, "add");
runover(detail) {
    printf("surface area: %.3f m²", f@area);
}
""", tags=("measure", "area", "detail")),

    R("rest_position", "Store rest position", "Utility",
      "Saves the current positions (for textures that should stick when deforming).",
      """
v@rest = v@P;
""", tags=("rest", "texture", "store")),

    # ── Smoke & Fire ─────────────────────────────────────────────────────────
    R("smoke_plume", "Smoke plume", "Smoke & Fire",
      "A real 3D smoke solver on OpenVDB grids: buoyancy, turbulence and vorticity confinement, "
      "made incompressible by Blender's Poisson solver and moved with MacCormack advection. "
      "Give the object a Principled Volume material.",
      """
// voxel box and size: ~200k voxels plays back interactively
float vs = chf("Domain/voxel_size", 0.04, min=0.01);
volumebounds(chv("Domain/min", {-1, -1, 0}), chv("Domain/max", {1, 1, 3}), vs);
vector src = chv("Source/center", {0, 0, 0.3});
float radius = chf("Source/radius", 0.25, min=0.01);

simulate {
    runover(voxel) {
        // emit a noisy puff, let the old smoke thin out and cool down
        float puff = smoothstep(radius, radius * 0.4, length(@P - src))
                   * fit(noise(@P * 4 + {0, 0, -2} * @Time), 0.3, 0.7, 0, 1);
        f@density = max(f@density * (1 - chf("dissipation", 0.08) * deltatime), puff);
        f@temperature = max(f@temperature * (1 - chf("cooling", 0.7) * deltatime), puff);
        // hot smoke rises; curl noise breaks up the column
        vector turb = curlnoise(@P - {0, 0, 1} * @Time * 0.3, chf("Turbulence/scale", 1.5));
        v@vel += ({0, 0, 1} * f@temperature * chf("buoyancy", 5)
                  + turb * chf("Turbulence/strength", 1.5) * f@density) * deltatime;
    }
    // vorticity confinement: feed energy back into the small swirls
    volumecurl("vel", "curl");
    runover(voxel) { f@swirl = length(v@curl); }
    runover(voxel) {
        vector towards = normalize(volumegradient(0, "swirl", @P) + {0, 0, 1e-6});
        v@vel += cross(towards, v@curl) * chf("vorticity", 1.5) * vs * deltatime;
    }
    volumeproject("vel");                        // incompressible: one Poisson solve
    volumeadvect("vel", "vel", deltatime);       // MacCormack
    volumeadvect("density", "vel", deltatime);
    volumeadvect("temperature", "vel", deltatime);
}
keep("volume");
""", tags=("smoke", "pyro", "fluid", "volume", "vdb", "simulation", "poisson"),
      needs=("a Principled Volume material on the object",), test={"kind": "plane", "frames": 3}),

    R("fire", "Fire", "Smoke & Fire",
      "Fuel pours from a disc, burns into heat and soot, and the heat drives the flames up. "
      "Render the temperature grid with the Principled Volume's blackbody emission.",
      """
float vs = chf("Domain/voxel_size", 0.03, min=0.01);
volumebounds(chv("Domain/min", {-0.75, -0.75, 0}), chv("Domain/max", {0.75, 0.75, 2.4}), vs);
vector src = chv("Fuel/center", {0, 0, 0.1});
float radius = chf("Fuel/radius", 0.3, min=0.01);

simulate {
    runover(voxel) {
        // a flat, flickering disc of fuel
        float disc = smoothstep(radius, radius * 0.4, length((@P - src) * {1, 1, 3}));
        float flicker = fit(noise(@P * 7 + {0, 0, -5} * @Time), 0.3, 0.7, 0, 1);
        float fuel = max(f@fuel, disc * flicker);
        // fuel burns into heat and soot; the heat cools and the soot thins out
        float burn = fuel * min(chf("Burn/rate", 8) * deltatime, 1);
        f@fuel = fuel - burn;
        f@temperature = min(f@temperature * (1 - chf("Burn/cooling", 2.2) * deltatime) + burn * chf("Burn/heat", 4), 2);
        f@density = f@density * (1 - chf("Smoke/dissipation", 0.5) * deltatime) + burn * chf("Smoke/soot", 0.5);
        vector turb = curlnoise(@P - {0, 0, 1} * @Time * 0.8, chf("Turbulence/scale", 3));
        v@vel += ({0, 0, 1} * f@temperature * chf("buoyancy", 7)
                  + turb * chf("Turbulence/strength", 4) * min(f@temperature, 1)) * deltatime;
    }
    volumecurl("vel", "curl");
    runover(voxel) { f@swirl = length(v@curl); }
    runover(voxel) {
        vector towards = normalize(volumegradient(0, "swirl", @P) + {0, 0, 1e-6});
        v@vel += cross(towards, v@curl) * chf("vorticity", 2.5) * vs * deltatime;
    }
    volumeproject("vel");
    volumeadvect("vel", "vel", deltatime);
    volumeadvect("fuel", "vel", deltatime);
    volumeadvect("temperature", "vel", deltatime);
    volumeadvect("density", "vel", deltatime);
}
keep("volume");
""", tags=("fire", "flame", "pyro", "combustion", "volume", "simulation"),
      needs=("a Principled Volume material with blackbody intensity",), test={"kind": "plane", "frames": 3}),

    R("smoke_ring", "Smoke ring", "Smoke & Fire",
      "A short push through a round hole rolls up into a vortex ring that travels and slowly grows — "
      "the incompressible solver does all the work, there's no buoyancy at all.",
      """
float vs = chf("Domain/voxel_size", 0.035, min=0.01);
volumebounds(chv("Domain/min", {-1.2, -1.2, 0}), chv("Domain/max", {1.2, 1.2, 3.6}), vs);
float radius = chf("Puff/radius", 0.35, min=0.05);
float height = chf("Puff/height", 0.3);

simulate {
    runover(voxel) {
        // a thin disc that pushes upward for the first few frames
        vector p = @P - {0, 0, height};
        float r = length(set(p.x, p.y, 0));
        float disc = smoothstep(radius, radius * 0.85, r) * smoothstep(vs * 2.5, 0, abs(p.z));
        float on = @Frame <= chi("Puff/frames", 8) ? 1 : 0;
        // smoke only near the rim: that's what rolls up into the ring
        f@density = max(f@density, disc * on * smoothstep(radius * 0.45, radius * 0.8, r));
        v@vel = lerp(v@vel, {0, 0, chf("Puff/speed", 3)}, disc * on);
    }
    volumeproject("vel");
    volumeadvect("vel", "vel", deltatime);
    volumeadvect("density", "vel", deltatime);
}
keep("volume");
""", tags=("smoke ring", "vortex", "toroidal", "fluid", "volume", "simulation"),
      needs=("a Principled Volume material on the object",), test={"kind": "plane", "frames": 3}),

    R("smoke_from_object", "Smoke from an object", "Smoke & Fire",
      "Smoke streams off the surface of the 'Emitter' object — animate it and the trail follows. "
      "Hot near the surface, so it rises and curls.",
      """
float vs = chf("Domain/voxel_size", 0.04, min=0.01);
volumebounds(chv("Domain/min", {-1.5, -1.5, 0}), chv("Domain/max", {1.5, 1.5, 3}), vs);

simulate {
    runover(voxel) {
        // a thin shell around the emitter's surface, broken up by noise
        float d = xyzdist(chobj("Emitter"), @P);
        float shell = smoothstep(chf("Emit/thickness", 0.06), 0, d)
                    * fit(noise(@P * 6 + {0, 0, -3} * @Time), 0.4, 0.7, 0, 1);
        f@density = max(f@density * (1 - chf("dissipation", 0.15) * deltatime), shell);
        f@temperature = max(f@temperature * (1 - chf("cooling", 1) * deltatime), shell);
        v@vel += {0, 0, 1} * f@temperature * chf("buoyancy", 4) * deltatime;
    }
    volumeproject("vel");
    volumeadvect("vel", "vel", deltatime);
    volumeadvect("density", "vel", deltatime);
    volumeadvect("temperature", "vel", deltatime);
}
keep("volume");
""", tags=("smoke", "emitter", "trail", "object", "volume", "simulation"),
      needs=("Emitter: the object that emits smoke", "a Principled Volume material"),
      test={"kind": "plane", "frames": 3, "inputs": {"Emitter": "cube"}}),

    # ── Liquids ──────────────────────────────────────────────────────────────
    R("shallow_water", "Shallow water (dam break)", "Liquids",
      "A column of water collapses and sloshes around a tank: the shallow-water equations with a "
      "MacCormack predictor–corrector. No pressure solve — the height IS the solution — and the time "
      "step adapts to the fastest wave, so it can't blow up.",
      """
int n = chi("resolution", 150, min=16);
float size = chf("size", 2, min=0.1);
grid(size, size, n, n);
float dx = size / (n - 1);
float g = chf("gravity", 9.8);

// U = (depth, x momentum, y momentum) and its fluxes in x and y
vector fx(vector U; float g) { return set(U.y, U.y * U.y / U.x + 0.5 * g * U.x * U.x, U.y * U.z / U.x); }
vector fy(vector U; float g) { return set(U.z, U.y * U.z / U.x, U.z * U.z / U.x + 0.5 * g * U.x * U.x); }
// a wall mirrors the water: same depth, momentum into the wall reversed
vector mirrorx(vector U) { return set(U.x, -U.y, U.z); }
vector mirrory(vector U) { return set(U.x, U.y, -U.z); }

simulate {
    init {
        float R = chf("Dam/radius", 0.3);
        float r = length(set(@P.x, @P.y, 0) - chv("Dam/center", {-0.45, -0.45, 0}));
        v@U = set(chf("depth", 0.15) + chf("Dam/height", 0.6) * smoothstep(R, R - dx * 3, r), 0, 0);
    }
    // time step from the fastest wave (CFL), split into as many substeps as needed
    float c = maxof(sqrt(g * v@U.x) + length(set(v@U.y, v@U.z, 0)) / v@U.x);
    int steps = int(clamp(ceil(deltatime * c / (chf("cfl", 0.5) * dx)), 1, 96));
    float k = deltatime / steps / dx;
    int ix = @ptnum / n;
    int iy = @ptnum % n;
    repeat(steps) {
        // predictor: forward differences in x and y together
        vector U = v@U;
        vector Ue = ix < n - 1 ? point(0, "U", @ptnum + n) : mirrorx(U);
        vector Un = iy < n - 1 ? point(0, "U", @ptnum + 1) : mirrory(U);
        v@Up = U - (fx(Ue, g) - fx(U, g)) * k - (fy(Un, g) - fy(U, g)) * k;
        // corrector: backward differences of the predicted state, averaged with the old one
        vector P = v@Up;
        vector Pw = ix > 0 ? point(0, "Up", @ptnum - n) : mirrorx(P);
        vector Ps = iy > 0 ? point(0, "Up", @ptnum - 1) : mirrory(P);
        vector next = (U + P - (fx(P, g) - fx(Pw, g)) * k - (fy(P, g) - fy(Ps, g)) * k) * 0.5;
        // keep a film of water everywhere and cap the speed of thin, fast spots
        float h = max(next.x, 0.002);
        vector q = set(next.y, next.z, 0);
        q *= min(1, chf("speed_cap", 3) * h / max(length(q), 1e-6));
        v@U = set(h, q.x, q.y);
        // a touch of viscosity damps the ripples that trail steep bores
        v@U = lerp(v@U, blur(v@U, 1), chf("smoothing", 0.05));
    }
}
f@depth = v@U.x;
f@speed = length(set(v@U.y, v@U.z, 0)) / v@U.x;
@P.z = f@depth * chf("height_scale", 1);
// deep water dark, crests light, fast water foams
f@foam = smoothstep(chf("Color/foam_speed", 0.7), chf("Color/foam_speed", 0.7) * 2, f@speed);
@Cd = lerp(colorramp("water", fit(f@depth, 0.08, 0.45, 0.1, 0.8), "ocean"), {1, 1, 1}, f@foam);
shadesmooth(1);
""", tags=("water", "shallow water", "dam break", "waves", "fluid", "simulation"), test={"frames": 3}),

    R("bubbles", "Bubbles", "Liquids",
      "Air bubbles rise through a tank: bigger ones rise faster, they spiral as they go, touching "
      "bubbles merge (their volumes add up) and they pop at the surface and start again at the bottom.",
      """
points(chi("count", 600, min=1));
vector tank = chv("Tank/size", {1.2, 1.2, 2});
float rmin = chf("Size/min", 0.01);
float rmax = chf("Size/max", 0.045);

simulate {
    init {
        @P = set((rand(@ptnum) - 0.5) * tank.x, (rand(@ptnum + 0.37) - 0.5) * tank.y, rand(@ptnum + 0.71) * tank.z);
        @pscale = lerp(rmin, rmax, pow(rand(@ptnum + 0.13), 3));
        f@seed = rand(@ptnum + 0.91);
    }
    // terminal speed grows with the square root of the size; a helix wobble on top
    float rise = chf("speed", 1.2) * sqrt(@pscale / 0.03);
    float phase = @Time * chf("Wobble/rate", 9) * (0.6 + f@seed) + f@seed * 50;
    vector wobble = set(cos(phase), sin(phase), 0) * @pscale * chf("Wobble/amount", 6);
    vector drift = curlnoise(@P + {1, 0, 0} * @Time * 0.4, chf("drift_scale", 1.2)) * chf("drift", 0.15);
    @P += (set(0, 0, rise) + wobble + drift) * deltatime;
    // merge: the smaller of two touching bubbles is swallowed by the bigger one
    int nb = nearpoint(0, @P);
    float nr = point(0, "pscale", nb);
    bool touching = distance(@P, point(0, "P", nb)) < (@pscale + nr) * 0.9;
    bool smaller = @pscale < nr || (@pscale == nr && @ptnum < nb);
    bool reborn = (touching && smaller) || @P.z > tank.z;
    if (touching && !smaller) {
        @pscale = min(pow(pow(@pscale, 3) + pow(nr, 3), 1.0 / 3), rmax * 2);
    }
    if (reborn) {
        float k = @Frame + f@seed * 1000;
        @P = set((rand(k) - 0.5) * tank.x, (rand(k + 0.37) - 0.5) * tank.y, 0);
        @pscale = lerp(rmin, rmax, pow(rand(k + 0.13), 3));
    }
}
""", tags=("bubbles", "water", "foam", "particles", "merge", "simulation"), test={"frames": 3}),

    R("ocean_waves", "Ocean waves (Gerstner)", "Liquids",
      "Six Gerstner waves summed on a grid: sharp crests, round troughs, crests that travel at the right "
      "speed for their length. Coloured by height with foam on the peaks.",
      """
float t = @Time * chf("speed", 1);
float steep = chf("steepness", 0.45, min=0, max=1);
vector offset = {0, 0, 0};
for (int i = 0; i < 6; i++) {
    float a = i * 2.4 + chf("direction", 0.3);
    vector dir = set(cos(a), sin(a), 0);
    float k = 2 * pi / (chf("wavelength", 2) / (1 + i * 0.45));
    float amp = chf("height", 0.12) / (1 + i * 0.6);
    float ph = k * (dot(dir, v@P) - sqrt(9.8 / k) * t);
    offset += set(dir.x * steep * amp * cos(ph), dir.y * steep * amp * cos(ph), amp * sin(ph));
}
@P += offset;
f@foam = smoothstep(chf("Foam/start", 0.12), chf("Foam/full", 0.25), offset.z);
@Cd = lerp(colorramp("water", fit(offset.z, -0.25, 0.25, 0, 1), "ocean"), {1, 1, 1}, f@foam);
shadesmooth(1);
""", tags=("ocean", "waves", "gerstner", "water", "sea"), needs=("a subdivided grid",),
      test={"kind": "grid"}),

    R("rain_ripples", "Rain on water", "Liquids",
      "Raindrops keep landing on a pond; each one starts a ring that spreads, crosses the others and fades.",
      """
simulate {
    init { f@h = 0; f@vel = 0; }
    // new drops every frame, at random points
    if (rand(@ptnum * 7 + int(@Frame) * 100003) < chf("rain", 0.0015)) {
        f@h = -chf("drop_depth", 1);
    }
    repeat(chi("steps", 3, min=1)) {
        float lap = blur(f@h, 1) - f@h;
        f@vel = (f@vel + lap * chf("speed", 1.6)) * (1 - chf("damping", 0.02));
        f@h += f@vel;
    }
}
@P.z += f@h * chf("height", 0.03);
@Cd = colorramp("water", fit(f@h, -0.3, 0.3, 0, 1), "ocean");
""", tags=("rain", "ripples", "pond", "water", "simulation"), needs=("a dense grid",),
      test={"kind": "grid", "frames": 3}),

    R("whirlpool", "Whirlpool", "Liquids",
      "A spinning funnel: the surface dips like a free vortex, and a spiral pattern turns around the core.",
      """
vector c = chv("center", {0, 0, 0});
vector d = set(@P.x - c.x, @P.y - c.y, 0);
float r = length(d);
float core = chf("core", 0.15, min=0.01);
@P.z -= chf("depth", 0.6) * core * core / (r * r + core * core);
float a = atan2(d.y, d.x) + chf("twist", 6) / (r + 0.2) - @Time * chf("spin", 2);
f@swirl = 0.5 + 0.5 * sin(a * chi("arms", 4));
@Cd = colorramp("water", fit(f@swirl * smoothstep(1.5, 0, r), 0, 1, 0.3, 1), "ocean");
""", tags=("whirlpool", "vortex", "maelstrom", "water", "deform"), needs=("a subdivided grid",),
      test={"kind": "grid"}),

    R("explosion", "Explosion", "Smoke & Fire",
      "A burst of hot fuel pushed outward in all directions for a few frames, then it burns, rises and "
      "rolls into a mushroom.",
      """
float vs = chf("Domain/voxel_size", 0.04, min=0.01);
volumebounds(chv("Domain/min", {-1.6, -1.6, 0}), chv("Domain/max", {1.6, 1.6, 3.6}), vs);
vector c = chv("Burst/center", {0, 0, 0.5});
float radius = chf("Burst/radius", 0.35, min=0.05);
simulate {
    runover(voxel) {
        float on = @Frame <= chi("Burst/frames", 4) ? 1 : 0;
        vector d = @P - c;
        float ball = smoothstep(radius, radius * 0.3, length(d)) * on;
        f@fuel = max(f@fuel, ball);
        v@vel += normalize(d + {0, 0, 1e-4}) * ball * chf("Burst/push", 40) * deltatime;
        float burn = f@fuel * min(chf("burn_rate", 5) * deltatime, 1);
        f@fuel -= burn;
        f@temperature = min(f@temperature * (1 - chf("cooling", 1.2) * deltatime) + burn * 5, 2);
        f@density = f@density * (1 - chf("dissipation", 0.15) * deltatime) + burn * chf("soot", 1.2);
        v@vel += {0, 0, 1} * f@temperature * chf("buoyancy", 6) * deltatime;
    }
    volumeproject("vel");
    volumeadvect("vel", "vel", deltatime);
    volumeadvect("fuel", "vel", deltatime);
    volumeadvect("temperature", "vel", deltatime);
    volumeadvect("density", "vel", deltatime);
}
keep("volume");
""", tags=("explosion", "fireball", "mushroom", "pyro", "volume", "simulation"),
      needs=("a Principled Volume material with blackbody intensity",), test={"kind": "plane", "frames": 3}),

    # ── Volumes ──────────────────────────────────────────────────────────────
    R("cloud", "Cloud from a mesh", "Volumes",
      "Turns any closed mesh into a cumulus cloud: the inside of the mesh, eroded by billowing noise. "
      "Model the cloud's shape with spheres, then add this.",
      """
float vs = chf("voxel_size", 0.025, min=0.005);
float pad = chf("Noise/amount", 0.2);
// signed distance (negative inside), exact out to the noise amount
volumefrommesh("sdf", vs, "sdf", pad * 2 + vs * 3);
volumebounds(getbbox_min(0) - pad, getbbox_max(0) + pad, vs);
runover(voxel) {
    float sd = volumesample(0, "sdf", @P);
    // billows: big round lumps that push out, fine noise that eats into the edge
    float lumps = fbm(@P, chf("Noise/scale", 2), 4, 0.5);
    float wisps = fbm(@P, chf("Noise/scale", 2) * 4, 3, 0.5);
    float edge = chf("softness", 0.04);
    f@density = smoothstep(edge, -edge, sd - (lumps - 0.45) * pad * 2 + (wisps - 0.5) * pad * 0.6);
}
volumeremove("sdf");
keep("volume");
""", tags=("cloud", "cumulus", "fog", "volume", "vdb"), needs=("a closed mesh",
                                                              "a Principled Volume material"),
      test={"kind": "ico"}),

    R("galaxy", "Spiral galaxy", "Volumes",
      "Thousands of points in spiral arms, splatted into glowing dust with Blender 5.3's Rasterize "
      "Points — colour too. The stars stay as points.",
      """
points(chi("stars", 20000, min=1));
int arms = chi("arms", 3, min=1);
float r = pow(rand(@ptnum), 0.6) * chf("radius", 1.5);
float arm = floor(rand(@ptnum + 0.1) * arms);
float spread = (rand(@ptnum + 0.2) - 0.5) * chf("spread", 0.7) / (r + 0.25);
float a = r * chf("twist", 3) + arm * 2 * pi / arms + spread;
@P = set(cos(a) * r, sin(a) * r, (rand(@ptnum + 0.3) - 0.5) * chf("thickness", 0.12) * (1.3 - r / 1.5));
@Cd = lerp({1, 0.72, 0.45}, {0.45, 0.6, 1}, smoothstep(0.1, 1.1, r));
@pscale = 0.004 * (0.5 + rand(@ptnum + 0.4));
float vs = chf("voxel_size", 0.02, min=0.003);
rasterize("density", 1, vs, "cubic");
rasterize("color", v@Cd, vs, "cubic");
volumeblur("density", 1, 2);
""", tags=("galaxy", "nebula", "space", "stars", "rasterize", "volume"),
      needs=("a volume material reading 'density' and 'color'",), blender=(5, 3, 0)),

    R("lava_lamp", "Lava lamp (metaballs)", "Volumes",
      "Blobs heat up at the bottom, rise, cool and sink. Each frame their spheres become one signed "
      "distance grid, smoothed so neighbours melt together, and then a mesh.",
      """
points(chi("blobs", 24, min=1));
float h = chf("Lamp/height", 2);
float w = chf("Lamp/radius", 0.35);
simulate {
    init {
        float a = rand(@ptnum) * 2 * pi;
        @P = set(cos(a), sin(a), 0) * w * 0.6 * rand(@ptnum + 0.5) + set(0, 0, rand(@ptnum + 0.7) * h);
        @pscale = lerp(chf("Blob/min", 0.08), chf("Blob/max", 0.18), rand(@ptnum + 0.3));
        f@heat = rand(@ptnum + 0.9);
    }
    // heated at the bottom, cooled at the top: warm wax rises, cold wax sinks
    f@heat = clamp(f@heat + (@P.z < h * 0.15 ? chf("heating", 0.5) : -chf("cooling", 0.12)) * deltatime, 0, 1);
    v@vel += set(0, 0, (f@heat - 0.5) * chf("buoyancy", 0.8)) * deltatime;
    v@vel *= 1 - chf("drag", 1.5) * deltatime;
    @P += v@vel * deltatime;
    // stay inside the glass
    vector flat = set(@P.x, @P.y, 0);
    if (length(flat) > w - @pscale) { @P = set(0, 0, @P.z) + normalize(flat) * (w - @pscale); }
    @P.z = clamp(@P.z, 0, h);
}
float vs = chf("voxel_size", 0.02, min=0.004);
volumefrompoints("wax", vs);
sdfsmooth("wax", chi("melt", 3), 2);
volumetomesh("wax", 0);
shadesmooth(1);
""", tags=("metaballs", "lava lamp", "blobs", "sdf", "volume", "simulation"), test={"frames": 3}),

    R("weta_bubbles", "Immersed bubbles (Wētā FX, EG 2025)", "Liquids",
      "Wretborn, Stomakhin & Batty, A unified multi-scale method for simulating immersed bubbles "
      "(Eurographics 2025). Massful air particles two-way coupled to the water: implicit drag (Eq. 36), a "
      "clamped air fraction, surface tension (CSF) and a mixture projection. Rising bubbles drag the water "
      "into a plume; crowded bubbles merge into pockets through the r_surfacing surface.",
      """
// A unified multi-scale method for simulating immersed bubbles
// Wretborn, Stomakhin & Batty — Wētā FX / University of Waterloo, Eurographics 2025 (CGF 44:2).
// Air is a set of massful particles two-way coupled to the water grid. One representation covers
// every scale: sparse particles are sub-grid bubbles; where they crowd, their volume fraction
// fills voxels and they act as one resolved pocket. Drag (Eq. 36) is integrated implicitly, so
// the 1:1000 air/water density ratio stays stable at any time step.
float h = chf("Tank/voxel_size", 0.015, min=0.003);
vector lo = chv("Tank/min", {-0.3, -0.3, 0});
vector hi = chv("Tank/max", {0.3, 0.3, 1});
volumebounds(lo, hi, h);
float rho_w = chf("Water/water_density", 1000);
float mu = chf("Water/viscosity", 0.001);
float sigma = chf("Water/surface_tension", 0.072);
float rho_b = chf("Air/air_density", 1.0);
float rmin = chf("Air/radius_min", 0.002);
float rmax = chf("Air/radius_max", 0.008);
vector nozzle = chv("Nozzle/center", {0, 0, 0.04});
float spread = chf("Nozzle/radius", 0.02);
float rate = chf("Nozzle/bubbles_per_second", 3000, min=1);
vector g = {0, 0, -9.81};
float cell = h * h * h;
points(chi("Air/particles", 20000, min=1));

int vents = chi("Nozzle/vents", 1, min=1);
float ring = chf("Nozzle/vent_ring", 0.15);
float period = chf("Nozzle/puff_period", 0, min=0);       // 0 = steady stream, else bursts ("exhale")

vector spawn(float id; vector nozzle; float spread; float vents; float ring) {
    float v = id % vents;
    float va = v / vents * 2 * pi + 0.7;
    vector c = nozzle + (vents > 1 ? set(cos(va), sin(va), 0) * ring : {0, 0, 0});
    float a = rand(id * 5 + 1) * 2 * pi;
    float r = sqrt(rand(id * 5 + 2)) * spread;
    return c + set(cos(a) * r, sin(a) * r, rand(id * 5 + 3) * spread);
}

simulate {
    init {
        // steady: one bubble every 1/rate s; bursts: a puff of period·rate bubbles every period
        float n = period > 0 ? period * rate : 1;
        f@birth = period > 0 ? floor(@ptnum / n) * period + rand(@ptnum * 5 + 6) * period * 0.25 : @ptnum / rate;
        @P = spawn(@ptnum, nozzle, spread, vents, ring);
        @pscale = lerp(rmin, rmax, pow(rand(@ptnum * 5 + 4), 2));
        v@ub = {0, 0, 0};
    }
    int substeps = chi("substeps", 3, min=1);
    float dt = deltatime / substeps;
    repeat(substeps) {
        float alive = @Time >= f@birth ? 1 : 0;
        float V = 4.0 / 3.0 * pi * pow(@pscale, 3);
        vector uw = volumesamplev(0, "vel", @P, "cubic");
        // Eq. 36 drag D = a(6 + k|s|)·s, a = μrπ/V, k = ρ_w r / 2μ, solved fully implicitly:
        // ρ_b (u' − u)/Δt = (ρ_b − ρ_w) g − D(u' − u_w)  ⇒  (ρ_b/Δt + a(6 + k|s|)) s = R,
        // a quadratic in |s| with a closed-form root — exact at any Δt and density ratio.
        float a = mu * @pscale * pi / V;
        float k = rho_w * @pscale / (2 * mu);
        vector R = (rho_b - rho_w) * g + rho_b / dt * (v@ub - uw);
        float B = rho_b / dt + 6 * a;
        float smag = (-B + sqrt(B * B + 4 * a * k * length(R))) / (2 * a * k);
        float c = a * (6 + k * smag);                         // drag coefficient at the new slip
        v@ub = alive * (uw + normalize(R) * smag);
        // path instability (beyond the paper): bubbles over ~1 mm zig-zag in their own wake,
        // shedding at a Strouhal number of ~0.2 — f = 0.2·|slip| / 2r
        float zig = 2 * pi * 0.2 * smag / (2 * @pscale) * @Time + rand(@ptnum * 5 + 7) * 6.283;
        v@ub += set(cos(zig), sin(zig), 0) * smag * chf("Air/zigzag", 0.35) * smoothstep(0.0008, 0.0015, @pscale);
        // particles → grid in one Rasterize Points pass: air fraction and the drag terms
        float w = alive * V / cell;
        rasterize("phi", w, 0, "linear");
        rasterize("K", w * c, 0, "linear");
        rasterize("Kub", v@ub * (w * c), 0, "linear");
        rasterize("phiub", v@ub * w, 0, "linear");
        // RK1 advection (as in the paper); bubbles that reach the surface pop and are re-emitted
        @P += v@ub * dt;
        if (@P.z > hi.z - h || abs(@P.x) > hi.x - h || abs(@P.y) > hi.y - h) {
            @P = spawn(@ptnum + @Frame * 7919, nozzle, spread, vents, ring);
            v@ub = {0, 0, 0};
        }
        runover(voxel) {
            float phi = min(f@phi, 1);                       // Eq. 31 clamp
            // implicit drag on the water: what the bubbles gained, the water loses (Eq. 36 reaction)
            vector uwat = (rho_w * v@vel + dt * v@Kub) / (rho_w + dt * f@K);
            // mixture velocity: air and water share one incompressible field
            vector uair = f@phi > 1e-6 ? v@phiub / f@phi : uwat;
            v@vel = lerp(uwat, uair, phi);
            f@air = phi;
        }
        // surface tension, continuum surface force: f = σ κ ∇φ with κ = −∇·(∇φ/|∇φ|)
        volumeblur("air", 1, 1);
        volumegrad("air", "gair");
        runover(voxel) { v@nrm = v@gair / max(length(v@gair), 1e-4); }
        volumedivergence("nrm", "curv");
        runover(voxel) {
            float rho = lerp(rho_w, rho_b, f@air);
            v@vel += v@gair * (-sigma * f@curv * dt / rho);
        }
        volumeproject("vel");
        volumeadvect("vel", "vel", dt);                       // MacCormack, as in the paper
    }
}
// hero surface: the particles' spheres dilated then eroded by r_surfacing = √(3/4)·h, so crowded
// bubbles merge into smooth pockets while isolated ones stay round
float rs = sqrt(0.75) * h * chf("Surface/merge", 1);
volumefrompoints("bubble", chf("Surface/surface_voxel", 0.003, min=0.0005), (@Time >= f@birth ? @pscale : 0) + rs);
sdfoffset("bubble", -rs);
volumetomesh("bubble", 0);
keep("mesh");
shadesmooth(1);
""", tags=("bubbles", "weta", "two-way coupling", "drag", "surface tension", "paper", "simulation"),
      needs=("a glass/water material with IOR 0.75 (air inside water)",), test={"kind": "plane", "frames": 2},
      blender=(5, 3, 0)),

    R("pfm_smoke", "Particle flow map smoke (SIGGRAPH 2024)", "Smoke & Fire",
      "Zhou et al., Eulerian-Lagrangian Fluid Simulation on Particle Flow Maps (ACM TOG 2024). Particles "
      "carry the flow-map Jacobian and an impulse m = Tᵀm₀, so vortices are transported instead of being "
      "re-sampled — they live far longer than with grid advection, and the smoke never blurs.",
      """
// Particle Flow Maps — Zhou, Chen, Deng, Deng, Sun, Wang, Xiong & Zhu, ACM TOG 43(4), SIGGRAPH 2024.
// Every particle carries the Jacobian T of the backward flow map and an impulse: m = Tᵀ·m₀.
// Vorticity is transported along the flow instead of being re-sampled every step, so swirls
// survive far longer than with grid advection. Smoke density rides on the particles: no blur.
float h = chf("Domain/voxel_size", 0.04, min=0.005);
vector lo = chv("Domain/min", {-1, -1, 0});
vector hi = chv("Domain/max", {1, 1, 3});
volumebounds(lo, hi, h);
vector cells = vceil((hi - lo) / h);
int ppc = chi("Particles/per_voxel", 2, min=1);
points(int(cells.x * cells.y * cells.z) * ppc);
int reinit = chi("Flow map/reinit_frames", 1, min=1);
vector src = chv("Source/center", {0, 0, 0.35});
float radius = chf("Source/radius", 0.25, min=0.01);

// uniform redistribution: each particle's jittered home cell
vector home(float id; float ppc; vector cells; vector lo; float h) {
    float c = floor(id / ppc);
    float i = c % cells.x;
    float j = floor(c / cells.x) % cells.y;
    float k = floor(c / (cells.x * cells.y));
    return lo + (set(i, j, k) - 0.5 + set(rand(id * 3 + 1), rand(id * 3 + 2), rand(id * 3 + 3))) * h;
}

simulate {
    init {
        @P = home(@ptnum, ppc, cells, lo, h);
        4@T = ident();
        // optional starting pushes through discs: each impulse rolls up into a vortex ring.
        // Two rings one behind the other leapfrog — the classic test of vortex preservation.
        float R = chf("Ring/ring_radius", 0.35);
        float push = 0;
        float smoke = 0;
        for (int k = 0; k < 2; k++) {
            vector q = @P - chv("Ring/ring_center", {0, 0, 0.4}) - set(0, 0, k * chf("Ring/spacing", 0.35));
            float on = k < chi("Ring/rings", 1) ? 1 : 0;
            float rr = length(set(q.x, q.y, 0));
            push += on * smoothstep(R, R * 0.85, rr) * smoothstep(0.08, 0, abs(q.z));
            smoke += on * smoothstep(chf("Ring/smoke_core", 0.09), 0, length(set(rr - R, q.z, 0)));
        }
        v@m0 = {0, 0, 1} * chf("Ring/ring_speed", 0) * push;
        v@impulse = {0, 0, 0};
        f@density = min(smoke, 1) * (chf("Ring/ring_speed", 0) > 0 ? 1 : 0);
    }
    // long-range reinitialisation: fresh map, impulse read back from the grid (G2P). Particles stay
    // where the flow took them (the flow is divergence-free, so they stay evenly spread) and keep
    // their smoke: the density is never re-sampled, so it never blurs. Optional uniform
    // redistribution (as in the paper) trades that sharpness for perfectly even sampling.
    if (@Frame > 1 && int(@Frame) % reinit == 0) {
        if (chi("Particles/redistribute", 0) == 1) {
            @P = home(@ptnum, ppc, cells, lo, h);
            f@density = volumesample(0, "density", @P, "linear");
        }
        v@m0 = volumesamplev(0, "vel", @P, "linear");
        4@T = ident();
        v@impulse = {0, 0, 0};
    }
    int substeps = chi("substeps", 3, min=1);
    float dt = deltatime / substeps;
    repeat(substeps) {
        // emit smoke; buoyancy adds to the impulse directly
        float puff = smoothstep(radius, radius * 0.5, length(@P - src))
                   * fit(noise(@P * 4 + {0, 0, -2} * @Time), 0.3, 0.7, 0, 1);
        f@density = max(f@density, puff);
        v@impulse += {0, 0, 1} * f@density * chf("buoyancy", 4) * dt;
        // impulse carried by the long-range flow map: m = Tᵀ m₀ (Eq. 15). Its gradient uses the
        // short-range map with n^S = 1 (the paper's 3D setting): ∇m from the grid each step (Eq. 19)
        vector m = mulvec(transpose(4@T), v@m0) + v@impulse;
        matrix Gm = volumejacobian(0, "vel", @P, h);
        float affine = chf("Flow map/affine_gradient", 1, min=0, max=1);
        matrix G = matrix3(matcol(Gm, 0) * affine, matcol(Gm, 1) * affine, matcol(Gm, 2) * affine);
        // particles → grid, affine like APIC (Eq. 17): m_i = Σw (m_p + ∇m_p (x_i − x_p)) / Σw.
        // Split as (Σw(m_p − ∇m_p x_p) + (Σw ∇m_p) x_i) / Σw, so ONE Blender 5.3 Rasterize Points pass
        // (aligned to the voxels) carries every channel, and the voxel step finishes the sum.
        rasterize("mS", m - mulvec(G, @P), 0, "linear");
        rasterize("G0", matcol(G, 0), 0, "linear");
        rasterize("G1", matcol(G, 1), 0, "linear");
        rasterize("G2", matcol(G, 2), 0, "linear");
        rasterize("W", 1, 0, "linear");
        rasterize("dW", f@density, 0, "linear");
        runover(voxel) {
            float wsum = max(f@W, 1e-6);
            v@vel = (v@mS + v@G0 * @P.x + v@G1 * @P.y + v@G2 * @P.z) / wsum;
            f@density = f@dW / wsum;
        }
        volumeproject("vel");                       // u = m − ∇φ with the 5.3 Poisson Solver
        // move the particles and evolve the map: dT/dt = −T·∇u with the midpoint velocity gradient.
        // Cayley step C = (I + ¼·½Δt∇u)⁻¹ (I − ¼·½Δt∇u), squared twice (scaling and squaring):
        // second order, and (I + …) stays far from singular even in the sharp strain of a ring core
        vector pm = @P + volumesamplev(0, "vel", @P) * (dt * 0.5);
        matrix J = volumejacobian(0, "vel", pm, h);
        vector j0 = matcol(J, 0) * (0.125 * dt);
        vector j1 = matcol(J, 1) * (0.125 * dt);
        vector j2 = matcol(J, 2) * (0.125 * dt);
        matrix ahead = matrix3({1, 0, 0} + j0, {0, 1, 0} + j1, {0, 0, 1} + j2);
        matrix behind = matrix3({1, 0, 0} - j0, {0, 1, 0} - j1, {0, 0, 1} - j2);
        matrix C = matmul(invert(ahead), behind);
        matrix C2 = matmul(C, C);
        4@T = matmul(4@T, matmul(C2, C2));
        @P = clamp(@P + volumesamplev(0, "vel", pm) * dt, lo, hi);
        // adaptive per-particle reinitialisation: a map stretched too far (|T − I| large) is restarted
        // from the projected grid velocity, so strong shear never amplifies particle noise
        float stretch = length(matcol(4@T, 0) - {1, 0, 0}) + length(matcol(4@T, 1) - {0, 1, 0})
                      + length(matcol(4@T, 2) - {0, 0, 1});
        if (stretch > chf("Flow map/max_stretch", 1.5, min=0.05)) {
            v@m0 = volumesamplev(0, "vel", @P, "linear");
            4@T = ident();
            v@impulse = {0, 0, 0};
        }
    }
}
keep("volume");
""", tags=("particle flow map", "impulse", "vortex", "smoke", "siggraph", "paper", "simulation"),
      needs=("a Principled Volume material",), test={"kind": "plane", "frames": 2}, blender=(5, 3, 0)),

    R("ground_fog", "Ground fog", "Volumes",
      "A layer of drifting fog over the object's area — thick at the ground, thinning with height, "
      "broken up by slow noise.",
      """
float vs = chf("voxel_size", 0.05, min=0.01);
float top = chf("height", 0.8);
volumebounds(getbbox_min(0) - {0.5, 0.5, 0}, set(getbbox_max(0).x + 0.5, getbbox_max(0).y + 0.5, getbbox_min(0).z + top), vs);
runover(voxel) {
    float h = (@P.z - getbbox_min(0).z) / top;
    float n = fbm(@P + set(@Time * chf("drift", 0.1), 0, 0), chf("scale", 1.5), 4, 0.55);
    f@density = smoothstep(1, 0, h) * smoothstep(0.35, 0.65, n) * chf("density", 1);
}
keep("volume");
""", tags=("fog", "mist", "atmosphere", "volume"), needs=("a Principled Volume material",)),

    R("smooth_union", "Smooth union (blobby merge)", "Volumes",
      "Joins the 'Input 1' object to this one and melts them together where they meet — like a smooth "
      "boolean union in an SDF modeller.",
      """
join(1);
float vs = chf("voxel_size", 0.02, min=0.004);
volumefrommesh("sdf", vs);
sdfsmooth("sdf", chi("blend", 4, min=1), 2);
volumetomesh("sdf", 0);
shadesmooth(1);
""", tags=("sdf", "boolean", "union", "blend", "melt", "remesh"), needs=("Input 1: the object to merge",),
      test={"inputs": {"Input 1": "cube"}}),

    R("remesh_smooth", "Voxel remesh", "Volumes",
      "Rebuilds the mesh from a distance grid: clean even topology, closed holes, optional rounding and "
      "growing or shrinking.",
      """
float vs = chf("voxel_size", 0.03, min=0.004);
volumefrommesh("sdf", vs);
sdfoffset("sdf", chf("offset", 0));
sdffillet("sdf", chi("fillet", 0, min=0));
volumetomesh("sdf", 0, chf("adaptivity", 0, min=0, max=1));
shadesmooth(1);
""", tags=("remesh", "voxel", "sdf", "clean", "topology")),

    R("phyllotaxis", "Phyllotaxis (sunflower)", "Scatter & Instance",
      "Points on the golden-angle spiral, like sunflower seeds or a pine cone — instance anything on them.",
      """
points(chi("count", 800, min=1));
float a = @ptnum * radians(chf("angle", 137.508));
float r = chf("spacing", 0.03) * sqrt(@ptnum);
@P = set(cos(a) * r, sin(a) * r, -chf("dome", 0.3) * r * r);
@pscale = chf("size", 0.02) * fit(@ptnum, 0, chi("count", 800), 0.5, 1.2);
p@orient = quaternion(a, {0, 0, 1});
""", tags=("phyllotaxis", "golden angle", "sunflower", "spiral", "points")),

    R("fibonacci_sphere", "Even points on a sphere", "Scatter & Instance",
      "Any number of almost perfectly evenly spaced points on a sphere (the Fibonacci lattice).",
      """
int n = chi("count", 500, min=1);
points(n);
float z = 1 - 2 * (@ptnum + 0.5) / n;
float a = @ptnum * pi * (3 - sqrt(5));
float r = sqrt(1 - z * z);
@P = set(cos(a) * r, sin(a) * r, z) * chf("radius", 1);
v@N = normalize(v@P);
@pscale = chf("size", 0.03);
""", tags=("fibonacci", "sphere", "even", "points", "lattice")),

    R("flag_wave", "Waving flag", "Deformers",
      "Waves travel along the flag from the pole and grow toward the free edge.",
      """
float u = relbbox(0, v@P).x;
float w = sin(@P.x * chf("frequency", 6) - @Time * chf("speed", 5)) * 0.6
        + sin(@P.y * 3 + @P.x * 2.3 - @Time * chf("speed", 5) * 0.7) * 0.4;
@P.z += w * chf("amplitude", 0.12) * pow(u, 1.5);
""", tags=("flag", "cloth", "wave", "wind", "deform"), needs=("a subdivided plane",), test={"kind": "grid"}),

    R("magnet", "Magnet", "Deformers",
      "Points near the 'Magnet' object are pulled toward it, with a smooth falloff.",
      """
vector m = objpos(chobj("Magnet"));
float d = distance(v@P, m);
float pull = smoothstep(chf("radius", 1), 0, d) * chf("strength", 0.6);
@P = lerp(v@P, m, clamp(pull, 0, 1));
""", tags=("magnet", "attract", "pull", "deform", "falloff"), needs=("Magnet: an object or empty",),
      test={"inputs": {"Magnet": "cube"}}),

    R("puddles", "Puddle mask", "Masks",
      "Water collects in the low, flat parts: 1 in puddles, 0 on bumps and slopes, with noisy edges.",
      """
float low = 1 - relbbox(0, v@P).z;
float flat = smoothstep(0.85, 0.98, dot(normalize(v@N), {0, 0, 1}));
float n = fbm(v@P, chf("scale", 3), 4, 0.5);
f@puddle = smoothstep(chf("level", 0.6), chf("level", 0.6) + 0.05, low * flat + (n - 0.5) * chf("breakup", 0.4));
""", tags=("puddles", "water", "wet", "mask", "rain")),

]

BY_KEY = {r.key: r for r in RECIPES}


def by_category():
    out = {c: [] for c in CATEGORIES}
    for r in RECIPES:
        out.setdefault(r.category, []).append(r)
    return out


def get(key):
    return BY_KEY.get(key)


def search(text):
    """Recipes matching every word first, then those matching some of them (best first) —
    so "attribute transfer" or a Houdini SOP name finds the closest tools even when not all words hit."""
    words = [w for w in text.lower().replace("_", " ").split() if w]
    if not words:
        return list(RECIPES)
    scored = []
    for r in RECIPES:
        hay = " ".join([r.key.replace("_", " "), r.title, r.category, r.description, " ".join(r.tags)]).lower()
        title = (r.title + " " + " ".join(r.tags)).lower()
        hit = sum(1 for w in words if w in hay)
        if hit:
            scored.append((hit == len(words), hit, sum(1 for w in words if w in title), r))
    scored.sort(key=lambda x: (not x[0], -x[1], -x[2]))
    full = [r for ok, _h, _t, r in scored if ok]
    return full if full else [r for _ok, _h, _t, r in scored[:8]]
