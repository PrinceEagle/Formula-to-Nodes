# SPDX-License-Identifier: GPL-3.0-or-later
"""Example scripts. Every one is compiled by the test suite, and a few are
used as few-shot examples in the AI prompt."""

EXAMPLES = [
    ("moss", "Moss on upward faces",
     "Float mask that is 1 where normals point up",
     'f@moss = pow(max(dot(v@N, {0, 0, 1}), 0), chf("sharpness", 2.0, min=0))'),

    ("wave", "Sine wave",
     "Animated ripple along X",
     'float amp = chf("amplitude", 0.2, min=0);\n'
     'float freq = chf("frequency", 3.0);\n'
     '@P.z += sin(@P.x * freq + @Time * 2) * amp;'),

    ("noise_disp", "Noise displacement",
     "Push points along normals with signed noise, store the amount",
     'float n = snoise(v@P, chf("scale", 2.0));\n'
     '@P += v@N * n * chf("strength", 0.3);\n'
     'f@displacement = n;'),

    ("twist", "Twist around Z",
     "Rotate each point by an angle that grows with height",
     'float angle = @P.z * chf("twist", 1.0);\n'
     '@P = rotate(v@P, {0, 0, 1}, angle);'),

    ("flatten", "Flatten below a floor",
     "Conditional write with if / else",
     'float floor_z = chf("floor", 0.0);\n'
     'if (@P.z < floor_z) {\n'
     '    @P.z = floor_z;\n'
     '    b@flattened = true;\n'
     '} else {\n'
     '    b@flattened = false;\n'
     '}'),

    ("rest", "Measure displacement",
     "Locals keep their value after writes (VEX semantics)",
     'vector rest = v@P;\n'
     '@P += v@N * chf("offset", 0.1);\n'
     'f@moved = distance(rest, v@P);'),

    ("radial", "Radial gradient mask",
     "Smooth falloff from the center in XY",
     'float r = length(v@P * {1, 1, 0});\n'
     'f@mask = 1 - smoothstep(chf("inner", 0.5), chf("outer", 1.5), r);'),

    ("pscale", "Random point scale",
     "Per-point random radius (useful before instancing)",
     '@pscale = fit01(rand(@ptnum + chi("seed", 0)), chf("min_size", 0.05), chf("max_size", 0.2));'),

    ("grow", "Grow over time (simulation)",
     "Accumulates frame to frame inside a simulation zone",
     'simulate {\n'
     '    @P += v@N * deltatime * chf("speed", 0.5);\n'
     '}'),

    ("spiral", "Spiral steps (repeat)",
     "Repeat zone: rotate and lift a little each iteration",
     'repeat(chi("steps", 5, min=0, max=100)) {\n'
     '    @P = rotate(v@P, {0, 0, 1}, chf("turn", 0.1)) + {0, 0, 0.05};\n'
     '}'),

    ("faces", "Jitter faces (foreach)",
     "For-each face: offset every face by its own random amount",
     'foreach(face) {\n'
     '    @P += v@N * rand(elemindex) * chf("max_offset", 0.2);\n'
     '}'),

    ("side", "Left / right mask",
     "Ternary expression",
     'f@side = @P.x > 0 ? 1 : -1;'),

    ("prim_colors", "Random colour per face",
     "#runover prim: lines run once per face",
     '#runover prim\n'
     '@Cd = hsvtorgb(set(rand(@primnum + chi("seed", 0)), 0.6, 0.9));'),

    ("cull", "Delete points above a height",
     "removepoint() is applied when the script ends",
     'if (@P.z > chf("height", 0.5)) removepoint(0, @ptnum);'),

    ("scatter", "Scatter and instance",
     "Scatter points, randomize them, copy an object onto them",
     'scatter(chf("density", 20), chi("seed", 0));\n'
     '@pscale = fit01(rand(@id), chf("Scale/min", 0.5), chf("Scale/max", 1.0));\n'
     'p@orient = qmultiply(p@orient, quaternion(rand(@id + 1) * 2 * pi, {0, 0, 1}));\n'
     'instance(chobj("Instance"));'),

    ("function", "Functions and loops",
     "A user function inside a for loop: three soft rings",
     'float ring(float d; float r; float w) {\n'
     '    return 1 - smoothstep(0, w, abs(d - r));\n'
     '}\n'
     'float d = length(v@P * {1, 1, 0});\n'
     'float sum = 0;\n'
     'for (int i = 1; i <= 3; i++) {\n'
     '    sum += ring(d, i * chf("spacing", 0.3), chf("width", 0.05));\n'
     '}\n'
     'f@rings = sum;'),

    ("detail", "Detail attribute",
     "One value for the whole geometry, then used per point",
     'setdetailattrib(0, "maxz", @P.z, "max");\n'
     'f@from_top = detail(0, "maxz") - @P.z;'),

    ("ramp", "Shaped by a curve",
     "chramp(): a curve you edit in the sidebar",
     'float t = relbbox(0, v@P).z;\n'
     '@P += v@N * chramp("bulge", t, "bell") * chf("amount", 0.3);'),
]

AI_FEW_SHOT = ("noise_disp", "flatten", "rest", "grow", "prim_colors", "scatter", "function", "detail")


def get(key):
    for e in EXAMPLES:
        if e[0] == key:
            return e
    return None
