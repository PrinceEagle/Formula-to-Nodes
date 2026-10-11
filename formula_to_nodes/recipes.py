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

CATEGORIES = ["Masks", "Deformers", "Scatter & Instance", "Growth & Simulation", "Smoke & Fire", "Liquids",
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
        vector turb = curlnoise(@P, chf("Turbulence/scale", 1.5), @Time * 0.3);
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
        vector turb = curlnoise(@P, chf("Turbulence/scale", 3), @Time * 0.8);
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
        float disc = smoothstep(radius, radius * 0.85, length(set(p.x, p.y, 0)))
                   * smoothstep(vs * 2.5, 0, abs(p.z));
        float on = @Frame <= chi("Puff/frames", 8) ? 1 : 0;
        f@density = max(f@density, disc * on);
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
      "two-step Lax–Wendroff scheme. No pressure solve — the height IS the solution — and the time "
      "step adapts to the fastest wave, so it can't blow up.",
      """
int n = chi("resolution", 150, min=16);
float size = chf("size", 2, min=0.1);
grid(size, size, n, n);
float dx = size / (n - 1);
float g = chf("gravity", 9.8);

// fluxes of U = (depth, x momentum, y momentum)
vector fx(vector U; float g) { return set(U.y, U.y * U.y / U.x + 0.5 * g * U.x * U.x, U.y * U.z / U.x); }
vector fy(vector U; float g) { return set(U.z, U.y * U.z / U.x, U.z * U.z / U.x + 0.5 * g * U.x * U.x); }

simulate {
    init {
        float R = chf("Dam/radius", 0.3);
        float r = length(set(@P.x, @P.y, 0) - chv("Dam/center", {-0.45, -0.45, 0}));
        v@U = set(chf("depth", 0.15) + chf("Dam/height", 0.6) * smoothstep(R, R - dx * 3, r), 0, 0);
    }
    // time step from the fastest wave (CFL), split into as many substeps as needed
    float c = maxof(sqrt(g * v@U.x) + length(set(v@U.y, v@U.z, 0)) / v@U.x);
    int steps = int(clamp(ceil(deltatime * c / (chf("cfl", 0.7) * dx)), 1, 64));
    float dt = deltatime / steps;
    int ix = @ptnum / n;
    int iy = @ptnum % n;
    repeat(steps) {
        // 1. half step to the east and north faces (walls mirror the momentum)
        vector U = v@U;
        vector Ue = ix < n - 1 ? point(0, "U", @ptnum + n) : set(U.x, -U.y, U.z);
        vector Un = iy < n - 1 ? point(0, "U", @ptnum + 1) : set(U.x, U.y, -U.z);
        v@Ux = (U + Ue) * 0.5 - (fx(Ue, g) - fx(U, g)) * (dt / (2 * dx));
        v@Uy = (U + Un) * 0.5 - (fy(Un, g) - fy(U, g)) * (dt / (2 * dx));
        // 2. full step from the face fluxes (the west and south walls only push back)
        vector wall = set(0, 0.5 * g * U.x * U.x, 0);
        vector Fw = ix > 0 ? fx(point(0, "Ux", @ptnum - n), g) : wall;
        vector Gs = iy > 0 ? fy(point(0, "Uy", @ptnum - 1), g) : set(0, 0, wall.y);
        vector next = U - (fx(v@Ux, g) - Fw) * (dt / dx) - (fy(v@Uy, g) - Gs) * (dt / dx);
        v@U = set(max(next.x, 0.001), next.y, next.z);
    }
    // a touch of viscosity keeps the bores clean
    v@U = lerp(v@U, blur(v@U, 1), chf("smoothing", 0.04));
}
f@depth = v@U.x;
f@speed = length(set(v@U.y, v@U.z, 0)) / v@U.x;
@P.z = f@depth * chf("height_scale", 1);
@Cd = colorramp("water", fit(f@speed, 0, chf("Color/max_speed", 2), 0, 1), "ocean");
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
    vector drift = curlnoise(@P, chf("drift_scale", 1.2), @Time * 0.4) * chf("drift", 0.15);
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

    # ── Volumes ──────────────────────────────────────────────────────────────
    R("cloud", "Cloud from a mesh", "Volumes",
      "Turns any closed mesh into a cumulus cloud: the inside of the mesh, eroded by billowing noise. "
      "Model the cloud's shape with spheres, then add this.",
      """
float vs = chf("voxel_size", 0.025, min=0.005);
float pad = chf("Noise/amount", 0.25);
volumefrommesh("inside", vs, "fog");
volumebounds(getbbox_min(0) - pad, getbbox_max(0) + pad, vs);
runover(voxel) {
    // signed distance: negative inside the mesh
    float d = xyzdist(0, @P);
    float sd = volumesample(0, "inside", @P) > 0.5 ? -d : d;
    // billows: big round lumps plus fine wisps
    float billow = abs(fbm(@P, chf("Noise/scale", 2.5), 5, 0.5)) * 2;
    float edge = chf("softness", 0.05);
    f@density = smoothstep(edge, -edge, sd + billow * pad - pad * 0.5);
}
volumeremove("inside");
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
