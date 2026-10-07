# SPDX-License-Identifier: GPL-3.0-or-later
"""Noise and random functions."""

from .core import FLOAT, INT, BOOL, VECTOR, Val, FUNCS, reg, FormulaError

reg("noise", "noise(pos [, scale, detail, roughness])", "Perlin noise 0..1 (float pos = 1D noise)",
    "Noise & random", "f_noise", mode="fac")
reg("snoise", "snoise(pos [, scale, detail, roughness])", "signed noise -1..1", "Noise & random", "f_noise",
    mode="signed")
reg("vnoise", "vnoise(pos [, scale, detail, roughness])", "noise as a vector, 0..1 per component",
    "Noise & random", "f_noise", mode="color")
reg("fbm", "fbm(pos [, scale, octaves, roughness])", "fractal Brownian motion, 0..1", "Noise & random",
    "f_noise", mode="fac", noise_type="FBM")
reg("ridged", "ridged(pos [, scale, octaves, roughness])", "ridged multifractal (sharp crests)",
    "Noise & random", "f_noise", mode="fac", noise_type="RIDGED_MULTIFRACTAL")
reg("flownoise", "flownoise(pos, flow [, scale])", "noise that evolves with flow (animate with @Time)",
    "Noise & random", "f_flownoise")
reg("curlnoise", "curlnoise(pos [, scale, detail])", "divergence-free swirling vector field (smoke-like flow)",
    "Noise & random", "f_curlnoise")
reg("worley wnoise", "NAME(pos [, scale, randomness])", "cellular (Voronoi) noise: distance to the nearest cell point",
    "Noise & random", "f_worley", feature_name="F1", out="Distance")
reg("worleyedge", "worleyedge(pos [, scale, randomness])", "distance to the nearest cell edge (cracks, tiles)",
    "Noise & random", "f_worley", feature_name="DISTANCE_TO_EDGE", out="Distance")
reg("cellrand", "cellrand(pos [, scale, randomness])", "a random 0..1 value per Voronoi cell",
    "Noise & random", "f_worley", feature_name="F1", out="Color")
reg("voronoi", "voronoi(pos [, scale, randomness])", "position of the nearest Voronoi cell point",
    "Noise & random", "f_worley", feature_name="F1", out="Position")
reg("anoise", "anoise(pos [, scale])", "alligator-like cellular bumps 0..1 (Voronoi based)",
    "Noise & random", "f_anoise")
reg("gabor", "gabor(pos [, scale, frequency, anisotropy])", "Gabor noise -1..1 (directional streaks, fabric)",
    "Noise & random", "f_gabor")
reg("rand", "rand(seed)", "0..1 random from a seed, e.g. rand(@ptnum); a vector when assigned to a vector",
    "Noise & random", "f_rand")
reg("random", "random() / random(seed) / random(min, max [, seed])", "per-element random value",
    "Noise & random", "f_random")
reg("hash", "hash(value [, seed])", "integer hash of a value (stable random ids)", "Noise & random", "f_hash")


class NoiseFuncs:
    def _noise_node(self, pos, scale=None, detail=None, rough=None, noise_type=None, w=None):
        inputs, field = {}, False
        if pos.t == VECTOR or w is not None:
            p = self.coerce(pos, VECTOR)
            inputs["Vector"] = self.inp(p, VECTOR)
            dims = "4D" if w is not None else "3D"
            field = p.field
        else:
            p = self.coerce(pos, FLOAT, "the noise position")
            inputs["W"] = self.inp(p, FLOAT)
            dims = "1D"
            field = p.field
        if w is not None:
            wv = self.coerce(w, FLOAT, "the flow")
            inputs["W"] = self.inp(wv, FLOAT)
            field = field or wv.field
        for key, v in (("Scale", scale), ("Detail", detail), ("Roughness", rough)):
            if v is not None:
                fv = self.coerce(v, FLOAT, f"the noise {key.lower()}")
                inputs[key] = self.inp(fv, FLOAT)
                field = field or fv.field
        props = {"noise_dimensions": dims}
        if noise_type:
            props["noise_type"] = noise_type
        n = self.g.add("ShaderNodeTexNoise", props, inputs)
        return n, field

    def f_noise(self, node, name):
        vals = self.args(node, name, {1, 2, 3, 4})
        d = FUNCS[name].data
        n, field = self._noise_node(vals[0], *vals[1:4], noise_type=d.get("noise_type"))
        mode = d["mode"]
        if mode == "color":
            return Val(VECTOR, o=n.out("Color"), field=field)
        fac = Val(FLOAT, o=n.out("Fac"), field=field)
        if mode == "signed":
            return self.math("SUBTRACT", self.math("MULTIPLY", fac, Val(FLOAT, c=2.0)), Val(FLOAT, c=1.0))
        return fac

    def f_flownoise(self, node, name):
        vals = self.args(node, name, {2, 3})
        n, field = self._noise_node(vals[0], vals[2] if len(vals) == 3 else None, w=vals[1])
        return Val(FLOAT, o=n.out("Fac"), field=field)

    def f_curlnoise(self, node, name):
        vals = self.args(node, name, {1, 2, 3})
        pos = self.coerce(vals[0], VECTOR, "curlnoise()'s position")
        scale = self.coerce(vals[1], FLOAT, "curlnoise()'s scale") if len(vals) > 1 else Val(FLOAT, c=5.0)
        detail = self.coerce(vals[2], FLOAT, "curlnoise()'s detail") if len(vals) > 2 else Val(FLOAT, c=0.0)
        eps = self.math("DIVIDE", Val(FLOAT, c=0.02), self.math("MAXIMUM", scale, Val(FLOAT, c=0.001)))

        def potential(offset):
            p = self.vmath("ADD", pos, offset)
            n, field = self._noise_node(p, scale, detail)
            return Val(VECTOR, o=n.out("Color"), field=field)

        derivs = []
        for axis in range(3):
            parts = [Val(FLOAT, c=0.0)] * 3
            parts = list(parts)
            parts[axis] = eps
            off = self.combine(parts)
            plus, minus = potential(off), potential(self.neg(off))
            d = self.vmath("SCALE", self.vmath("SUBTRACT", plus, minus),
                           scale=self.math("DIVIDE", Val(FLOAT, c=0.5), self.math("MULTIPLY", eps, scale)))
            derivs.append(d)
        dx, dy, dz = derivs
        cx = self.math("SUBTRACT", self.sep(dy, 2), self.sep(dz, 1))
        cy = self.math("SUBTRACT", self.sep(dz, 0), self.sep(dx, 2))
        cz = self.math("SUBTRACT", self.sep(dx, 1), self.sep(dy, 0))
        return self.combine([cx, cy, cz])

    def _voronoi(self, pos, scale, randomness, feature):
        p = self.coerce(pos, VECTOR, "the noise position")
        inputs = {"Vector": self.inp(p, VECTOR)}
        field = p.field
        for key, v in (("Scale", scale), ("Randomness", randomness)):
            if v is not None:
                fv = self.coerce(v, FLOAT, f"the {key.lower()}")
                inputs[key] = self.inp(fv, FLOAT)
                field = field or fv.field
        n = self.g.add("ShaderNodeTexVoronoi", {"voronoi_dimensions": "3D", "feature": feature}, inputs)
        return n, field

    def f_worley(self, node, name):
        vals = self.args(node, name, {1, 2, 3})
        d = FUNCS[name].data
        n, field = self._voronoi(vals[0], vals[1] if len(vals) > 1 else None,
                                 vals[2] if len(vals) > 2 else None, d["feature_name"])
        out = d["out"]
        if out == "Distance":
            return Val(FLOAT, o=n.out("Distance"), field=field)
        if out == "Position":
            return Val(VECTOR, o=n.out("Position"), field=field)
        return self.sep(Val(VECTOR, o=n.out("Color"), field=field), 0)

    def f_anoise(self, node, name):
        vals = self.args(node, name, {1, 2})
        n, field = self._voronoi(vals[0], vals[1] if len(vals) > 1 else None, None, "SMOOTH_F1")
        dist = Val(FLOAT, o=n.out("Distance"), field=field)
        bump = self.math("MAXIMUM", self.math("SUBTRACT", Val(FLOAT, c=1.0), dist), Val(FLOAT, c=0.0))
        return self.math("MULTIPLY", bump, bump)

    def f_gabor(self, node, name):
        vals = self.args(node, name, {1, 2, 3, 4})
        p = self.coerce(vals[0], VECTOR, "gabor()'s position")
        inputs, field = {"Vector": self.inp(p, VECTOR)}, p.field
        for key, v in zip(("Scale", "Frequency", "Anisotropy"), vals[1:]):
            fv = self.coerce(v, FLOAT, f"gabor()'s {key.lower()}")
            inputs[key] = self.inp(fv, FLOAT)
            field = field or fv.field
        n = self.g.add("ShaderNodeTexGabor", {"gabor_type": "3D"}, inputs)
        return Val(FLOAT, o=n.out("Value"), field=field)

    def f_rand(self, node, name):
        (seed,) = self.args(node, name, {1})
        want_vec = self.hinted(node) == VECTOR
        if seed.t == VECTOR:
            sid = self.f_hash_val(seed, None)
        else:
            sid = self.coerce(seed, INT, "rand()'s seed")
        if want_vec:
            n = self.g.add("FunctionNodeRandomValue", {"data_type": "FLOAT_VECTOR"},
                           {"Min": (0.0, 0.0, 0.0), "Max": (1.0, 1.0, 1.0),
                            "ID": self.inp(sid, INT), "Seed": 0})
            return Val(VECTOR, o=n.out("Value"), field=sid.field)
        n = self.g.add("FunctionNodeRandomValue", {"data_type": "FLOAT"},
                       {"Min": 0.0, "Max": 1.0, "ID": self.inp(sid, INT), "Seed": 0})
        return Val(FLOAT, o=n.out("Value"), field=sid.field)

    def f_random(self, node, name):
        vals = self.args(node, name, {0, 1, 2, 3})
        if len(vals) == 1:
            return self.f_rand(node, "rand")
        inputs = {}
        if len(vals) >= 2:
            inputs["Min"] = self.inp(vals[0], FLOAT, "random()'s min")
            inputs["Max"] = self.inp(vals[1], FLOAT, "random()'s max")
        if len(vals) == 3:
            inputs["Seed"] = self.inp(vals[2], INT, "random()'s seed")
        n = self.g.add("FunctionNodeRandomValue", {"data_type": "FLOAT"}, inputs)
        return Val(FLOAT, o=n.out("Value"), field=True)

    def f_hash(self, node, name):
        vals = self.args(node, name, {1, 2})
        return self.f_hash_val(vals[0], vals[1] if len(vals) > 1 else None)

    def f_hash_val(self, v, seed):
        dtype = {FLOAT: "FLOAT", INT: "INT", BOOL: "BOOLEAN", VECTOR: "VECTOR", "STRING": "STRING",
                 "ROTATION": "ROTATION", "MATRIX": "MATRIX"}.get(v.t)
        if dtype is None:
            raise FormulaError("hash() works on numbers, vectors, rotations and text")
        inputs = {"Value": self.inp(v, v.t)}
        field = v.field
        if seed is not None:
            sv = self.coerce(seed, INT, "hash()'s seed")
            inputs["Seed"] = self.inp(sv, INT)
            field = field or sv.field
        n = self.g.add("FunctionNodeHashValue", {"data_type": dtype}, inputs)
        return Val(INT, o=n.out("Hash"), field=field)
