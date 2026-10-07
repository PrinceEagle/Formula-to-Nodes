// Signed distance functions: negative inside, positive outside
//   #include "sdf.h"
//   f@d = sdsmoothunion(sdsphere(v@P, 1), sdbox(v@P - {1, 0, 0}, {0.5, 0.5, 0.5}), 0.3);

float sdsphere(vector p; float r) {
    return length(p) - r;
}

float sdbox(vector p; vector halfsize) {
    vector q = vabs(p) - halfsize;
    return length(vmax(q, {0, 0, 0})) + min(max(q.x, max(q.y, q.z)), 0);
}

float sdtorus(vector p; float major; float minor) {
    float qx = length(set(p.x, p.y, 0)) - major;
    return length(set(qx, p.z, 0)) - minor;
}

float sdcapsule(vector p; vector a; vector b; float r) {
    vector pa = p - a;
    vector ba = b - a;
    float h = clamp(dot(pa, ba) / max(dot(ba, ba), 0.000001), 0, 1);
    return length(pa - ba * h) - r;
}

float sdplane(vector p; vector normal; float offset) {
    return dot(p, normalize(normal)) - offset;
}

float sdunion(float a; float b) {
    return min(a, b);
}

float sdsmoothunion(float a; float b; float k) {
    return smoothmin(a, b, k);
}

float sdsubtract(float a; float b) {
    return max(a, -b);
}

float sdintersect(float a; float b) {
    return max(a, b);
}
