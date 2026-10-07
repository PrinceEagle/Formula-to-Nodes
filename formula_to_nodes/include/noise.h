// Noise recipes: domain warping and terrain
//   #include "noise.h"

vector warp(vector p; float scale; float amount) {
    return p + (vnoise(p, scale) - {0.5, 0.5, 0.5}) * amount;
}

float warpednoise(vector p; float scale; float amount) {
    return noise(warp(p, scale, amount), scale);
}

// heights from fractal + ridged noise, flat in Z
float terrain(vector p; float scale; float height) {
    vector q = p * {1, 1, 0};
    float base = fbm(q, scale, 6, 0.5);
    float crests = ridged(q, scale * 0.5, 4, 0.5);
    return (base * 0.7 + crests * 0.3) * height;
}

// marble-like bands bent by noise
float marble(vector p; float scale; float turbulence) {
    return 0.5 + 0.5 * sin((p.x + noise(p, scale) * turbulence) * scale);
}
