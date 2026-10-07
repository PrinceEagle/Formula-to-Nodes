// Colour helpers: cosine palettes (after Inigo Quilez) and random colours
//   #include "color.h"
//   @Cd = palette_sunset(relbbox(0, v@P).z);

vector palette(float t; vector a; vector b; vector c; vector d) {
    vector ph = (c * t + d) * 6.283185;
    return a + b * vcos(ph);
}

vector palette_rainbow(float t) {
    return palette(t, {0.5, 0.5, 0.5}, {0.5, 0.5, 0.5}, {1, 1, 1}, {0, 0.33, 0.67});
}

vector palette_sunset(float t) {
    return palette(t, {0.5, 0.5, 0.5}, {0.5, 0.5, 0.5}, {1, 1, 0.5}, {0.8, 0.9, 0.3});
}

vector palette_ocean(float t) {
    return palette(t, {0.2, 0.5, 0.6}, {0.2, 0.3, 0.3}, {1, 1, 1}, {0.0, 0.1, 0.2});
}

vector palette_forest(float t) {
    return palette(t, {0.4, 0.5, 0.25}, {0.25, 0.3, 0.15}, {1, 1, 1}, {0.05, 0.1, 0.15});
}

// blue → green → yellow → red
vector heatmap(float t) {
    return hsvtorgb(set((1 - clamp(t, 0, 1)) * 0.66, 0.9, 1));
}

vector randcolor(int seed; float saturation; float value) {
    return hsvtorgb(set(rand(seed), saturation, value));
}
