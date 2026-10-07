# Formula to Nodes — listing description (v2.1)

## Stop wiring Math nodes by hand.

`sin(r) * amp` is six nodes and eight connections in Geometry Nodes. A real effect is thirty.
Type the formula instead and get the group.

```
float r = length(v@P)
@P.z = sin(r * freq) * amp
```

Two lines. `freq` and `amp` appear as sliders automatically. What you get is an ordinary,
editable node group — open it, rewire it, learn from it. Nothing is baked or hidden.

The syntax is VEX-flavoured, so a Houdini wrangle translates directly. If you've never
written one, it reads like the formula you'd write on paper.

## What it does

- **Single-line formulas** or **multi-line scripts** with typed local variables, edited in the
  panel or the Text Editor. Writes chain top to bottom, like a wrangle.
- **Branching** — `if / else if / else`, ternaries `? :`, comparisons, `&& || !`.
- **Zones** — `simulate { }` for anything that accumulates over time (`deltatime` included),
  `repeat(n) { }`, and `foreach(face) { }` across point, edge, face, curve and instance domains.
- **Parameters** — `chf("amp", 0.5, min=0)`, plus `chi` / `chv` / `chb`, become real inputs on
  the node with defaults and ranges.
- **99 functions** — trig, exp, log, atan2, noise, random, clamp, lerp, smoothstep, fit,
  rotate, reflect, project, refract, and the rest.
- **Reads and writes real geometry** — `@P`, `@N`, `@id`, `@pscale`, `@ptnum`, `@Time`,
  tangent, curve parameter, each through its correct dedicated node. Writing `@N`, `@id` and
  `@pscale` is supported, not faked.
- **Rebuild in place** — regenerating keeps the group's name, its links and your modifier
  values. *Edit Selected Group* loads a group's script back into the panel.
- **Tidy output** — constants are folded, repeated sub-expressions are shared, and the graph is
  laid out left to right with zones respected.
- **Check before you build** — errors come back with line numbers and the bad line highlighted.

## AI Assist

Describe the effect; the add-on writes the script. Every reply is compiled first, and compile
errors are sent back to the model to fix, so what lands in your panel already builds. Works
with Claude, OpenAI, Ollama or any OpenAI-compatible server. No API key? **Copy Prompt for Any
Chatbot** gives you the whole language guide to paste anywhere — paste the reply back and the
code fences are stripped for you.

## Claude Desktop & Claude Code (MCP)

With Blender Lab's **MCP** add-on running, one button in the sidebar connects Claude Desktop.
Ask for an effect in plain language and it builds the nodes in your open file, then reads the
evaluated result back to check its own work. It runs on Blender's bundled Python — nothing
else to install.

*Sidebar → Formula → Claude Desktop (MCP) → Connect Claude Desktop, then restart Claude.*

## New in 2.1

A full rewrite with a real compiler. Ten cases that silently produced **wrong geometry** in
1.0.x are fixed — including vector math collapsing to a scalar in nested expressions,
`v ** 2`, `abs()` on vectors, `log(x)`, and locals losing their value after a later write.
Groups are also smaller: the same legacy scripts now build with fewer nodes.

Blender 5.2 · GPL-3.0-or-later · pure Python, no bundled binaries
