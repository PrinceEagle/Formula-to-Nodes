# Formula to Nodes 3.0.0

A much bigger language, closer to Houdini VEX, plus recipes and nodes → script.

## Language
- **Run over any domain**: `#runover prim` (or vertex, edge, curve, instance, detail) and
  `runover(prim) { ... }` blocks. Detail attributes (`runover(detail)`, `detail()`,
  `setdetailattrib()`) are stored in geometry bundles.
- **Types**: rotations (`vector4`/`p@`), 4x4 matrices (`matrix`/`4@`), colours (`c@`), 2D UVs (`u@`),
  strings (`s@` text attributes need Blender 5.3), objects/collections/materials/images/sounds as parameters.
- **Arrays** (Blender 5.2 lists): `float vals[] = {1, 2, 3};`, indexing, `len`, `append`, `insert`,
  `removeindex`, `resize`, `sort`, `sum`, `min`/`max`/`avg`, `find`, `foreach (float v; vals)`.
- **Functions** with typed parameters (passed by reference, like VEX), `return`, overloads.
- **Loops**: C-style `for`, `foreach`, `repeat(n)`; short fixed loops unroll, others become repeat zones.
- **Preprocessor**: `#define`, `#include` (bundled `falloff.h`, `sdf.h`, `shaping.h`, `color.h`,
  `noise.h`, or your own text blocks), `#pragma`.
- **Geometry operations**: scatter, instance, realize, subdivide, subdivsurf, triangulate, dualmesh,
  convexhull, fuse, extrude, bevel, resample, sweep, tocurves, topoints, points, grid, join, transform,
  setmaterial, shadesmooth, flipfaces, sortpoints, removepoint/removeprim, addpoint.
- **185 new function names** (99 → 284): lookups (`point`, `prim`, `nearpoint`, `xyzdist`, `primuv`, `intersect`,
  ray casts, `volumesample`, `texture`), topology (`neighbour`, `primpoint`, ...), aggregates
  (`sumof`, `avgof`, `minof`, ... with an optional group or domain), rotations and matrices
  (`quaternion`, `slerp`, `dihedral`, `lookat`, `maketransform`, ...), colour, text (`sprintf`,
  `printf`/`warning`/`error` shown as node warnings), noise (`curlnoise`, `flownoise`, `gabor`, worley).
- **Ramps**: `chramp("name", x, "preset")` / `colorramp(...)` with presets; edited shapes survive rebuilds.
- **Parameters** get panels (`chf("Folder/name")`), ranges and tooltips (`min=`, `max=`, `tip=`).

## Recipes
51 ready-made scripts in 9 categories (masks, deformers, scatter & instance, growth & simulation,
effectors, colour, curves, modeling, utility): Script panel › **Recipes**, or `formula_list_recipes` over MCP.

## Nodes → script
**Convert Nodes to Script** turns any Geometry Nodes group into a script (like VOPs → wrangle).
Anything without an equivalent is listed in notes, never silently changed.

## Blender versions
Capability checks tell you which features your Blender has (string attributes need 5.3); the AI
prompt and the function reference only show what the running Blender supports.

## AI / MCP
- Default model `claude-sonnet-5-5`, with server-side fallback to another model when it's overloaded,
  clearer refusals, 16k output tokens and a 180 s timeout.
- New MCP tools: `formula_list_recipes`, `formula_get_recipe`, `formula_capabilities`, `formula_decompile`.

# Formula to Nodes 2.1.0

## New: Claude Desktop / Claude Code via MCP
Works together with Blender Lab's **MCP** add-on (the bridge inside Blender).
- Sidebar › Formula › **Claude Desktop (MCP)** › *Connect Claude Desktop* adds
  `formula-to-nodes` to Claude's config (macOS, Windows incl. Microsoft Store).
  Existing servers are kept and a `.bak` backup is written. Restart Claude.
- Runs on Blender's own bundled Python: nothing else to install.
- Tools Claude gets: language guide, check script, build (and apply to an
  object), inspect evaluated attribute values, set parameters, list groups,
  get a group's source, scene overview.
- *Copy JSON* / *Claude Code* buttons for other MCP clients.

# Formula to Nodes 2.0.0

## Fixed (all verified by evaluating geometry in Blender 5.2)
- Vector math in nested expressions no longer collapses to a scalar: `(a + b) * 0.5`
- Unary minus, `abs()`, `floor()` etc. on vectors are now per-component
- `v ** 2`, `%` and `//` no longer silently turn into `+`
- `log(x)` is the natural log (it used base 0.5)
- Trailing `//` comments no longer create fake sliders or syntax errors
- `refract()` IOR went into the wrong socket
- `curveparam` crashed in 5.2 (Curve Parameter node was renamed)
- Locals now keep their value after later writes, like VEX
- Locals declared in simulate/foreach blocks can't leak out into invalid links
- Constants on implicit-input sockets (Set Position, Noise, Random ID…) were ignored
- Vector values stored into float attributes are now an error instead of an average
- "Replace Active" could delete hand-made node groups

## New
- if / else if / else, ternary `? :`, comparisons, `&& || !`
- `repeat(n) { }` zones, nested zones, `return` for field outputs
- Vector literals `{x, y, z}`, VEX aliases `@P @N @ptnum @pscale @Time …`
- `chf/chi/chv/chb` parameters with defaults and min/max
- Writes to `@N` (custom normals), `@id`, `@pscale`, `f@tilt`
- 99 functions incl. fit01, efit, smooth, snoise, rand, relbbox, npoints
- Smaller trees: constants inlined, repeated sub-expressions shared
- Automatic left-to-right layout that respects zones
- Updating a group keeps its name, links and modifier values
- Edit Selected Group loads a group's source back into the panel
- Check button, line-numbered errors, error line highlighted
- Examples menu
- AI Assist: Claude, OpenAI, Ollama or any OpenAI-compatible server;
  every reply is compiled and errors are sent back for a fix
- Copy Prompt for Any Chatbot (no API key needed)
