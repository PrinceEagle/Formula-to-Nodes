// Falloff shapes for effectors and masks: 1 at the centre, 0 at the radius
//   #include "falloff.h"
//   f@w = falloff_sphere(v@P, chv("center", {0, 0, 0}), chf("radius", 1), 0.5);

float falloff_linear(float d; float radius) {
    return clamp(1 - d / max(radius, 0.0001), 0, 1);
}

float falloff_smooth(float d; float radius) {
    return 1 - smoothstep(0, max(radius, 0.0001), d);
}

float falloff_gauss(float d; float radius) {
    float s = max(radius, 0.0001) / 3;
    return exp(-(d * d) / (2 * s * s));
}

// softness 0 = hard edge, 1 = fades from the centre
float falloff_sphere(vector p; vector center; float radius; float softness) {
    float d = distance(p, center);
    return 1 - smoothstep(radius * (1 - clamp(softness, 0, 1)), radius, d);
}

float falloff_box(vector p; vector center; vector size; float softness) {
    vector q = vabs(p - center) - size * 0.5;
    float outside = length(vmax(q, {0, 0, 0}));
    return 1 - smoothstep(0, max(softness, 0.0001), outside);
}

// 0 behind the plane, 1 in front, blended over width
float falloff_plane(vector p; vector origin; vector normal; float width) {
    float d = dot(p - origin, normalize(normal));
    return smoothstep(-width * 0.5, width * 0.5, d);
}

// ring around an axis through center
float falloff_ring(vector p; vector center; vector axis; float radius; float width) {
    vector rel = p - center;
    vector a = normalize(axis);
    float h = dot(rel, a);
    float r = length(rel - a * h);
    float d = length(set(r - radius, h, 0));
    return 1 - smoothstep(0, max(width, 0.0001), d);
}
