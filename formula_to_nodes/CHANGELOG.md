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
