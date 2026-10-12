# Formula to Nodes 3.1 — promo copy

Numbers are measured: node counts are the groups the add-on built, "fps simulated" is the
simulation's own speed with a fresh cache (render time not included), on an Apple M5 Pro.
Smoke, fire and the smoke ring ran on Blender 5.3 beta (Poisson Solver); the rest on 5.2 LTS.

## Launch post (web / Blender Market / Gumroad)

**Formula to Nodes 3.1 — typed simulations for Blender 5.3**

Type a few lines, get an ordinary, editable Geometry Nodes group. 3.1 adds volumes:
`runover(voxel) { }` works like a Houdini Volume Wrangle, and whole-grid operations —
advect, project, curl, blur, rasterize — map straight onto Blender's OpenVDB nodes. On Blender 5.3
`volumeproject()` is a single Poisson Solver node instead of hundreds of Jacobi iterations, so a
real 3D smoke solver is about 30 lines and simulates at ~10 fps at its default ~190k voxels.

- 72 recipes in 12 categories: smoke, fire, explosions, smoke rings, shallow water, ocean waves,
  bubbles, rain, clouds, galaxies, metaballs, voxel remesh, smooth unions…
- Blender 5.3 nodes used automatically: Poisson Solver, Rasterize Points, Combine List,
  Get Vector Component, Miter Scale, Preserve Normals — with fallbacks on 5.2.
- AI Assist now knows how Geometry Nodes is fast (built-in solvers, one write per grid, CFL-safe
  time steps), so generated scripts are built the efficient way.
- Convert Nodes to Script, 20 colour-ramp and 15 curve presets, Claude Desktop / MCP tools.

## Per-shot captions (Instagram)

**3D smoke, typed** — 783k-voxel smoke plume from one script: buoyancy, curl-noise turbulence and
vorticity confinement in a Volume Wrangle, made incompressible by Blender 5.3's Poisson Solver.
144 nodes, 3.2 fps simulated. #b3d #geometrynodes #blender53 #vfx #simulation

**Fire from a formula** — fuel burns into heat and soot, heat lifts the flames, the Principled
Volume turns temperature into blackbody light. 164 nodes, 200k voxels, 6.7 fps simulated.
#b3d #geometrynodes #fire #pyro

**One push → a vortex ring** — no buoyancy, no keyframes: a disc pushes for eight frames and the
incompressible solver rolls it into a travelling ring. 69 nodes, 490k voxels, 8.7 fps simulated.
#b3d #fluidsim #geometrynodes

**Bubbles that merge and pop** — bigger bubbles rise faster, spiral, swallow the smaller ones they
touch (volumes add up) and pop at the surface. 900 bubbles, 143 nodes. #b3d #geometrynodes #particles

**Dam break, one script** — shallow-water equations with a MacCormack predictor–corrector; substeps
come from the fastest wave so it can't blow up. 22,500 cells, 188 nodes, 42 fps simulated.
#b3d #watersim #geometrynodes

**Open ocean in one loop** — six Gerstner waves summed, foam where the crests peak.
163 nodes. #b3d #ocean #geometrynodes

**Lava lamp metaballs** — blobs heat at the bottom, rise, cool and sink; their spheres become one
signed distance grid, smoothed so they melt together. 91 nodes, 143 fps simulated. #b3d #metaballs

**Any mesh → a cloud** — model the shape with a few spheres; a distance field eroded by two layers
of noise turns it into a cumulus. 45 nodes. #b3d #clouds #volumetrics

**A galaxy from points** — 20,000 points in spiral arms, splatted into glowing dust with
Blender 5.3's Rasterize Points (colour too). 64 nodes. #b3d #blender53 #space

## Story / Reel cover line

"Type the formula. Get the node group."

## Research-paper carousels (promo/paper_cards, Blender 5.3 beta)

**Wētā FX's bubble solver, typed** — Wretborn, Stomakhin & Batty, *A unified multi-scale method for
simulating immersed bubbles* (Eurographics 2025), as an 85-line script → 301 Geometry Nodes. Massful
air particles two-way coupled to the water: one Rasterize Points pass, the paper's Eq. 36 drag solved
exactly, CSF surface tension, Poisson projection. A single bubble rises at 0.224 m/s vs 0.226 m/s
from the drag law. ~0.4 s per simulated frame. #b3d #geometrynodes #blender53 #vfx #fluidsim #weta

**Particle flow maps in Geometry Nodes** — Zhou et al., *Eulerian-Lagrangian Fluid Simulation on
Particle Flow Maps* (SIGGRAPH 2024): particles carry the flow-map Jacobian and impulse m = Tᵀm₀, with
affine P2G in a single Rasterize Points node and the 5.3 Poisson Solver. Two vortex rings collide;
smoke keeps 101% of its peak density after 60 frames vs 83% with grid (MacCormack) advection.
79 lines → 329 nodes. #b3d #geometrynodes #blender53 #siggraph #smokesim
