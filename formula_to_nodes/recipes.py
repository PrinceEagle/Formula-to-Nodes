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

CATEGORIES = ["Masks", "Deformers", "Scatter & Instance", "Growth & Simulation", "Effectors", "Color",
              "Curves", "Modeling", "Utility", "Research Papers"]


class Recipe:
    __slots__ = ("key", "title", "category", "description", "script", "tags", "needs", "test")

    def __init__(self, key, title, category, description, script, tags=(), needs=(), test=None):
        self.key, self.title, self.category, self.description = key, title, category, description
        self.script, self.tags, self.needs, self.test = script.strip() + "\n", tuple(tags), tuple(needs), test or {}

    def as_dict(self):
        return {"key": self.key, "title": self.title, "category": self.category,
                "description": self.description, "tags": list(self.tags), "needs": list(self.needs),
                "script": self.script}


R = Recipe
RECIPES = [
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
        v@vel = v@N * chf("launch", 3) + set(snoise(v@P, 4), snoise(v@P + 5, 4), 0) * chf("spread", 1);
    }
    v@vel += set(0, 0, -chf("gravity", 9.8)) * deltatime;
    v@vel *= 1 - chf("drag", 0.1) * deltatime;
    @P += v@vel * deltatime;
    if (@P.z < chf("ground", 0)) {
        @P.z = chf("ground", 0);
        v@vel = set(v@vel.x, v@vel.y, -v@vel.z * chf("bounce", 0.5));
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

    R("wave_pool", "Wave pool", "Growth & Simulation",
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
]


# ── Research papers, rebuilt as scripts ─────────────────────────────────────
RECIPES += [
    R('paper_smoke', 'Smoke solver (Stable Fluids)', "Research Papers",
      'Stable Fluids (Stam 1999) with vorticity confinement (Fedkiw, Stam & Jensen 2001): a 2D smoke solver with semi-Lagrangian advection, buoyancy and a Jacobi pressure projection in one simulation zone.',
      '// Stable Fluids (Stam 1999) + vorticity confinement (Fedkiw, Stam & Jensen 2001)\nint gidx(int i; int j; int n) {\n    return clamp(i, 0, n - 1) * n + clamp(j, 0, n - 1);\n}\n\n// bilinear lookup of a grid field at a fractional cell position\nfloat gsample(float q; float x; float y; int n) {\n    float cx = clamp(x, 0, n - 1.001);\n    float cy = clamp(y, 0, n - 1.001);\n    int i0 = int(floor(cx));\n    int j0 = int(floor(cy));\n    float sx = cx - i0;\n    float sy = cy - j0;\n    return lerp(lerp(atindex(q, gidx(i0, j0, n)), atindex(q, gidx(i0 + 1, j0, n)), sx),\n                lerp(atindex(q, gidx(i0, j0 + 1, n)), atindex(q, gidx(i0 + 1, j0 + 1, n)), sx), sy);\n}\n\nint n = chi("Grid/resolution", 96, min=16);\ngrid(chf("Grid/size", 2), chf("Grid/size", 2), n, n);\n\nsimulate {\n    int i = int(floor(@ptnum / n));\n    int j = @ptnum - i * n;\n    float dt = chf("Solver/dt", 1);\n\n    // smoke source near the bottom\n    float src = 1 - smoothstep(n * 0.035, n * 0.06, length(set(i - n * 0.5, j - n * 0.1, 0)));\n    f@density = max(f@density, src);\n    v@vel.y += src * chf("Smoke/inflow", 0.5);\n\n    // buoyancy\n    v@vel.y += dt * chf("Smoke/buoyancy", 0.06) * f@density;\n\n    // vorticity confinement: push along N x w, N = grad|w| / |grad|w||\n    f@w = 0.5 * ((atindex(v@vel.y, gidx(i + 1, j, n)) - atindex(v@vel.y, gidx(i - 1, j, n)))\n               - (atindex(v@vel.x, gidx(i, j + 1, n)) - atindex(v@vel.x, gidx(i, j - 1, n))));\n    float gx = 0.5 * (abs(atindex(f@w, gidx(i + 1, j, n))) - abs(atindex(f@w, gidx(i - 1, j, n))));\n    float gy = 0.5 * (abs(atindex(f@w, gidx(i, j + 1, n))) - abs(atindex(f@w, gidx(i, j - 1, n))));\n    float gl = max(sqrt(gx * gx + gy * gy), 0.00001);\n    v@vel += dt * chf("Smoke/vorticity", 0.35) * set(gy / gl * f@w, -gx / gl * f@w, 0);\n\n    // semi-Lagrangian advection: trace back, sample the old fields\n    float bx = i - dt * v@vel.x;\n    float by = j - dt * v@vel.y;\n    float ux = gsample(v@vel.x, bx, by, n);\n    float uy = gsample(v@vel.y, bx, by, n);\n    float d = gsample(f@density, bx, by, n);\n    v@vel = set(ux, uy, 0);\n    f@density = d * chf("Smoke/dissipation", 0.996);\n\n    // projection: divergence, Jacobi pressure solve, subtract the gradient\n    f@div = 0.5 * ((atindex(v@vel.x, gidx(i + 1, j, n)) - atindex(v@vel.x, gidx(i - 1, j, n)))\n                 + (atindex(v@vel.y, gidx(i, j + 1, n)) - atindex(v@vel.y, gidx(i, j - 1, n))));\n    repeat(chi("Solver/pressure_iterations", 40)) {\n        f@p = (atindex(f@p, gidx(i + 1, j, n)) + atindex(f@p, gidx(i - 1, j, n))\n             + atindex(f@p, gidx(i, j + 1, n)) + atindex(f@p, gidx(i, j - 1, n)) - f@div) * 0.25;\n    }\n    v@vel -= 0.5 * set(atindex(f@p, gidx(i + 1, j, n)) - atindex(f@p, gidx(i - 1, j, n)),\n                       atindex(f@p, gidx(i, j + 1, n)) - atindex(f@p, gidx(i, j - 1, n)), 0);\n\n    // closed box\n    if (i == 0 || j == 0 || i == n - 1 || j == n - 1) v@vel = {0, 0, 0};\n}\n@Cd = colorramp("smoke", f@density, "magma");\n',
      tags=('paper', 'smoke', 'fluid', 'simulation', 'grid')),
    R('paper_ocean', 'Ocean waves (Tessendorf)', "Research Papers",
      "Ocean waves after Tessendorf's Simulating Ocean Water (2001): a Phillips spectrum of choppy Gerstner waves with deep-water dispersion, and foam where the surface folds.",
      '// Tessendorf (2001), "Simulating Ocean Water": a Phillips-spectrum sum of choppy\n// (Gerstner) waves with deep-water dispersion, and foam where the surface folds\ngrid(chf("Grid/size", 60), chf("Grid/size", 60), chi("Grid/resolution", 160), chi("Grid/resolution", 160));\nfloat g = 9.81;\nfloat wind = radians(chf("Wind/direction", 30));\nfloat L = chf("Wind/speed", 9) * chf("Wind/speed", 9) / g;\nfloat chop = chf("choppiness", 1.5);\nfloat t = @Time * chf("time_scale", 1);\nint seed = chi("seed", 7);\nvector disp = {0, 0, 0};\nfloat dxx = 0;\nfloat dyy = 0;\nfloat dxy = 0;\nrepeat(chi("Spectrum/waves", 96)) {\n    float r1 = rand(iteration * 3 + seed * 1000);\n    float r2 = rand(iteration * 3 + 1 + seed * 1000);\n    float r3 = rand(iteration * 3 + 2 + seed * 1000);\n    float wl = chf("Spectrum/longest", 30) * pow(chf("Spectrum/shortest", 0.8) / chf("Spectrum/longest", 30), r1);\n    float k = 2 * PI / wl;\n    float th = wind + (r2 - 0.5) * PI * 0.8;\n    vector kd = set(cos(th), sin(th), 0);\n    // Phillips spectrum sampled log-uniformly in k: amplitude ~ sqrt(P(k)) * k\n    float a = chf("amplitude", 0.035) * exp(-0.5 / (k * k * L * L)) / k * abs(cos(th - wind));\n    float ph = k * dot(kd, v@P) - sqrt(g * k) * t + r3 * 2 * PI;\n    disp += set(-kd.x * chop * a * sin(ph), -kd.y * chop * a * sin(ph), a * cos(ph));\n    float c = chop * a * k * cos(ph);\n    dxx -= kd.x * kd.x * c;\n    dyy -= kd.y * kd.y * c;\n    dxy -= kd.x * kd.y * c;\n}\n@P += disp;\n// Jacobian of the horizontal displacement: small where waves pinch, negative where they fold\nfloat J = (1 + dxx) * (1 + dyy) - dxy * dxy;\nf@foam = 1 - smoothstep(chf("Foam/min", 0.5), chf("Foam/max", 0.9), J);\nvector water = lerp(chv("Colors/deep", 0.004, 0.025, 0.05), chv("Colors/crest", 0.02, 0.16, 0.2), smoothstep(-0.5, 0.8, disp.z));\n@Cd = lerp(water, chv("Colors/foam", 0.9, 0.93, 0.95), f@foam);\nshadesmooth(1);\n',
      tags=('paper', 'ocean', 'water', 'waves', 'spectrum')),
    R('paper_lbm', 'Vortex street (Lattice Boltzmann)', "Research Papers",
      "A Lattice Boltzmann fluid (D2Q9 with BGK collision, Qian, d'Humieres & Lallemand 1992): flow past a cylinder sheds a von Karman vortex street, coloured by vorticity.",
      '// Lattice Boltzmann, D2Q9 with BGK collision (Qian, d\'Humieres & Lallemand 1992):\n// flow past a cylinder sheds a von Karman vortex street\nfloat feq(float w; float rho; float cu; float uu) {\n    return w * rho * (1 + 3 * cu + 4.5 * cu * cu - 1.5 * uu);\n}\nint at(int i; int j; int nx; int ny) {\n    return clamp(i, 0, nx - 1) * ny + (j + ny) % ny;\n}\n\nint nx = chi("Grid/cells_x", 160, min=16);\nint ny = chi("Grid/cells_y", 64, min=8);\ngrid(chf("Grid/size", 2.5), chf("Grid/size", 2.5) * (ny - 1.0) / (nx - 1.0), nx, ny);\n\nsimulate {\n    init {\n        float u0 = chf("Flow/inflow_speed", 0.08);\n        float q = u0 * u0;\n        f@f0 = feq(4.0 / 9, 1, 0, q);\n        f@f1 = feq(1.0 / 9, 1, u0, q);\n        f@f2 = feq(1.0 / 9, 1, 0, q);\n        f@f3 = feq(1.0 / 9, 1, -u0, q);\n        f@f4 = feq(1.0 / 9, 1, 0, q);\n        f@f5 = feq(1.0 / 36, 1, u0, q);\n        f@f6 = feq(1.0 / 36, 1, -u0, q);\n        f@f7 = feq(1.0 / 36, 1, -u0, q);\n        f@f8 = feq(1.0 / 36, 1, u0, q);\n    }\n    int i = int(floor(@ptnum / ny));\n    int j = @ptnum - i * ny;\n    float U = chf("Flow/inflow_speed", 0.08);\n    float omega = 1 / (0.5 + 3 * chf("Flow/viscosity", 0.012));\n    int solid = length(set(i - nx * 0.22, j - ny * 0.53, 0)) < chf("Obstacle/radius", 0.09) * ny;\n    repeat(chi("Flow/steps_per_frame", 10)) {\n        // streaming: every population moves one cell along its direction\n        f@f1 = atindex(f@f1, at(i - 1, j, nx, ny));\n        f@f2 = atindex(f@f2, at(i, j - 1, nx, ny));\n        f@f3 = atindex(f@f3, at(i + 1, j, nx, ny));\n        f@f4 = atindex(f@f4, at(i, j + 1, nx, ny));\n        f@f5 = atindex(f@f5, at(i - 1, j - 1, nx, ny));\n        f@f6 = atindex(f@f6, at(i + 1, j - 1, nx, ny));\n        f@f7 = atindex(f@f7, at(i + 1, j + 1, nx, ny));\n        f@f8 = atindex(f@f8, at(i - 1, j + 1, nx, ny));\n        // bounce-back inside the cylinder\n        if (solid) {\n            float a1 = f@f1; float a2 = f@f2; float a3 = f@f3; float a4 = f@f4;\n            float a5 = f@f5; float a6 = f@f6; float a7 = f@f7; float a8 = f@f8;\n            f@f1 = a3; f@f3 = a1; f@f2 = a4; f@f4 = a2;\n            f@f5 = a7; f@f7 = a5; f@f6 = a8; f@f8 = a6;\n        }\n        // collision: relax towards the local equilibrium (inflow cells are set to it)\n        float rho = f@f0 + f@f1 + f@f2 + f@f3 + f@f4 + f@f5 + f@f6 + f@f7 + f@f8;\n        float ux = i == 0 ? U : (f@f1 + f@f5 + f@f8 - f@f3 - f@f6 - f@f7) / rho;\n        float uy = i == 0 ? 0 : (f@f2 + f@f5 + f@f6 - f@f4 - f@f7 - f@f8) / rho;\n        rho = i == 0 ? 1 : rho;\n        float uu = ux * ux + uy * uy;\n        float om = i == 0 ? 1 : omega;\n        if (solid == 0) {\n            f@f0 += om * (feq(4.0 / 9, rho, 0, uu) - f@f0);\n            f@f1 += om * (feq(1.0 / 9, rho, ux, uu) - f@f1);\n            f@f2 += om * (feq(1.0 / 9, rho, uy, uu) - f@f2);\n            f@f3 += om * (feq(1.0 / 9, rho, -ux, uu) - f@f3);\n            f@f4 += om * (feq(1.0 / 9, rho, -uy, uu) - f@f4);\n            f@f5 += om * (feq(1.0 / 36, rho, ux + uy, uu) - f@f5);\n            f@f6 += om * (feq(1.0 / 36, rho, uy - ux, uu) - f@f6);\n            f@f7 += om * (feq(1.0 / 36, rho, -ux - uy, uu) - f@f7);\n            f@f8 += om * (feq(1.0 / 36, rho, ux - uy, uu) - f@f8);\n        }\n    }\n    float r2 = f@f0 + f@f1 + f@f2 + f@f3 + f@f4 + f@f5 + f@f6 + f@f7 + f@f8;\n    v@vel = set((f@f1 + f@f5 + f@f8 - f@f3 - f@f6 - f@f7) / r2, (f@f2 + f@f5 + f@f6 - f@f4 - f@f7 - f@f8) / r2, 0);\n    i@solid = solid;\n}\n// vorticity, for display\nint ci = int(floor(@ptnum / ny));\nint cj = @ptnum - ci * ny;\nf@curl = (atindex(v@vel.y, at(ci + 1, cj, nx, ny)) - atindex(v@vel.y, at(ci - 1, cj, nx, ny)))\n       - (atindex(v@vel.x, at(ci, cj + 1, nx, ny)) - atindex(v@vel.x, at(ci, cj - 1, nx, ny)));\nfloat vv = clamp(f@curl * chf("Display/gain", 25), -1, 1);\n@Cd = vv > 0 ? lerp({0.02, 0.02, 0.03}, {1, 0.42, 0.08}, vv) : lerp({0.02, 0.02, 0.03}, {0.12, 0.5, 1}, -vv);\nif (i@solid) @Cd = {0.85, 0.85, 0.85};\n',
      tags=('paper', 'fluid', 'lattice boltzmann', 'vortex', 'simulation')),
    R('paper_lorenz', 'Lorenz attractor', "Research Papers",
      'The Lorenz attractor (Lorenz 1963): one trajectory integrated with fourth-order Runge-Kutta in a repeat zone, coloured by speed.',
      '// Lorenz (1963), "Deterministic Nonperiodic Flow": one trajectory, RK4\nvector lorenz(vector p; float s; float r; float b) {\n    return set(s * (p.y - p.x), p.x * (r - p.z) - p.y, p.x * p.y - b * p.z);\n}\n\nint n = chi("points", 4000, min=10);\npoints(n);\nfloat s = chf("sigma", 10);\nfloat r = chf("rho", 28);\nfloat b = chf("beta", 2.6667);\nfloat h = chf("dt", 0.004);\nv@x = {0.1, 0, 0};\nrepeat(n) {\n    if (iteration < @ptnum) {\n        vector p = v@x;\n        vector k1 = lorenz(p, s, r, b);\n        vector k2 = lorenz(p + k1 * h * 0.5, s, r, b);\n        vector k3 = lorenz(p + k2 * h * 0.5, s, r, b);\n        vector k4 = lorenz(p + k3 * h, s, r, b);\n        v@x = p + (k1 + 2 * k2 + 2 * k3 + k4) * h / 6;\n    }\n}\n@P = set(v@x.x, v@x.y, v@x.z - 25) * 0.05;\n@Cd = colorramp("speed", fit(length(lorenz(v@x, s, r, b)), 0, 160, 0, 1), "fire");\n@pscale = chf("radius", 0.011);\n',
      tags=('paper', 'chaos', 'attractor', 'ode', 'math')),
    R('paper_spheretrace', 'Sphere-traced Mandelbulb', "Research Papers",
      'Sphere tracing (Hart 1996) of the Mandelbulb: one ray per grid point marches to the fractal surface and leaves a point there.',
      '// Sphere tracing (Hart 1996) of the Mandelbulb distance estimator:\n// one ray per grid point, marched until it touches the fractal\nfloat bulb(vector pos; float power) {\n    vector z = pos;\n    float dr = 1;\n    float r = length(z);\n    for (int k = 0; k < 7; k++) {\n        if (r < 2) {\n            float theta = acos(clamp(z.z / max(r, 0.000001), -1, 1)) * power;\n            float phi = atan2(z.y, z.x) * power;\n            dr = pow(r, power - 1) * power * dr + 1;\n            z = pow(r, power) * set(sin(theta) * cos(phi), sin(theta) * sin(phi), cos(theta)) + pos;\n            r = length(z);\n        }\n    }\n    return 0.5 * log(max(r, 0.000001)) * r / dr;\n}\n\nint n = chi("resolution", 140, min=16);\nfloat size = chf("size", 2.6);\ngrid(size, size, n, n);\nfloat power = chf("power", 8);\nvector ro = set(@P.x, @P.y, 2);\nvector rd = {0, 0, -1};\nfloat t = 0;\nrepeat(chi("steps", 48)) {\n    float d = bulb(ro + rd * t, power);\n    if (d > chf("epsilon", 0.002) && t < 4) t += d;\n}\nvector hit = ro + rd * t;\nif (bulb(hit, power) > 0.01) removepoint(0, @ptnum);\n@P = hit;\nf@depth = hit.z;\ntopoints();\n@Cd = colorramp("palette", fit(f@depth, -1.1, 1.1, 0, 1), "magma");\n@pscale = size / n * 0.75;\n',
      tags=('paper', 'fractal', 'sdf', 'raymarching', 'math')),
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
    words = text.lower().split()
    hits = []
    for r in RECIPES:
        hay = " ".join([r.key, r.title, r.category, r.description, " ".join(r.tags)]).lower()
        if all(w in hay for w in words):
            hits.append(r)
    return hits
