// Shaping curves for 0..1 values (pulses, waves, impulses)
//   #include "shaping.h"

float pulse(float x; float center; float width) {
    return 1 - smoothstep(0, max(width * 0.5, 0.0001), abs(x - center));
}

float sawtooth(float x) {
    return frac(x);
}

float triwave(float x) {
    return 1 - abs(frac(x) * 2 - 1);
}

float squarewave(float x; float duty) {
    return frac(x) < duty ? 1 : 0;
}

float sharpen(float x; float amount) {
    return clamp((x - 0.5) * (1 + amount) + 0.5, 0, 1);
}

// rises fast, decays slowly; peaks (1) at x = 1 / k
float expimpulse(float x; float k) {
    float h = k * x;
    return h * exp(1 - h);
}

float parabola(float x; float k) {
    return pow(max(4 * x * (1 - x), 0), k);
}

float remap01(float x; float lo; float hi) {
    return clamp((x - lo) / (hi - lo), 0, 1);
}
