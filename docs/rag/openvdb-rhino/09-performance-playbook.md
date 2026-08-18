# Performance Playbook — Ordered Actions for a Slow Dendro / OpenVDB Definition

Compiled for Simplifyber engineering, August 2026, from the sources cited across this
collection. Ordered by return on effort, cheapest and most reversible first.

Before tuning anything: **measure the two halves separately.** A Crystallon-plus-Dendro
definition has a geometry-generation half running interpreted on the Grasshopper solver
thread and a volumetric half running in native threaded code. They have opposite remedies,
and guessing which one dominates wastes the whole tuning session. The Grasshopper profiler
widget, or simply disconnecting downstream and re-solving, will separate them.

## Step 0 — Stop paying the display cost

The single largest interactive-feel win, and it changes no geometry at all.

| Action | Why |
|---|---|
| Disable preview on every Dendro Volume to Mesh output while working | A multi-million-face lattice mesh is re-drawn by the Rhino display pipeline on every viewport change, not once per solve. This cost is often larger than the solve itself. |
| Lock the Grasshopper solver before changing several inputs, then unlock | Re-solves once instead of once per change. Advice given directly by Michael Pryor on the McNeel forum for exactly this class of definition. |
| Insert a Data Dam upstream of the volumetric section | Holds upstream edits until released, so exploratory changes to the lattice parameters do not each trigger a full voxelisation. |
| Internalise the input geometry | Removes any Rhino document reference cost and makes the definition reproducible for benchmarking. |

## Step 1 — Check for the Trim Lattice bug before believing any timing

If the definition uses Crystallon Trim Lattice, confirm **both** of its outputs — trimmed
beams and untrimmed beams — are feeding Curve to Volume. Connecting only the first output
silently drops the entire lattice interior, producing a hollow shell. See
`06-crystallon-lattice-generation.md`.

This is listed here because it makes a definition *faster* while producing a wrong result,
which corrupts every measurement taken afterwards.

## Step 2 — Free settings changes, in order

| Setting change | Cost multiplier | Notes |
|---|---|---|
| Lower Bandwidth toward 2 in Create Settings | Linear. Going from 6 to 3 halves the active voxel count. | Below about 2, OpenVDB does not guarantee zero crossings. The vendor's own advice: "keep this value as small as possible in order to minimize computation time." |
| Raise Adaptivity to 0.1–0.3 in Create Settings | Does not speed up the volume work; cuts output face count substantially | Flat lattice regions get large polygons, curved regions keep detail. Everything downstream — display, Weaverbird, baking, STL — gets proportionally cheaper. |
| Switch Smooth Volume Type to 1 (Laplacian) | Cheapest of the four filters on a true SDF, and identical in result to mean-curvature flow | Gaussian is internally 4 mean passes; Median is non-separable and documented as "relatively slow". Note Width has no effect on Laplacian. |
| Use a single Create Settings component for every converter | Removes a full `GridTransformer`/`BoxSampler` resample inside every boolean between mismatched grids | The vendor advises this for consistency; the resample is the mechanical reason it matters. |
| Raise Voxel Size while working, lower it only for the final bake | Quadratic — halving voxel size is a 4× penalty | The vendor's own advice: "Keep this value as large as possible while working and decrease it as a final step." |

## Step 2b — Check voxel size against the thinnest feature, not against instinct

`03-voxel-cost-model.md` gives the **floor**: roughly three voxels across a strut radius
before the strut looks faceted. The matching **ceiling** is the check almost nobody runs,
and over-resolution is more common than under-resolution.

The rule: **four to six voxels across the thinnest feature is sufficient** for a smooth
surface out of marching cubes. Beyond that, the extra voxels are resolving detail the
geometry does not contain.

```
voxels across the thinnest feature  =  thinnest feature thickness / voxel size
```

Because cost scales as 1/s², over-resolution is punishingly expensive:

| Voxels across a 1.5 mm wall | Voxel size | Relative volumetric cost |
|---|---|---|
| 4 voxels across a 1.5 mm wall | 0.375 mm | 1× |
| 6 voxels across a 1.5 mm wall | 0.25 mm | 2.3× |
| 10 voxels across a 1.5 mm wall | 0.15 mm | 6.3× |
| 15 voxels across a 1.5 mm wall | 0.10 mm | 14× |

A definition sitting at 15 voxels across its thinnest wall is paying roughly **fourteen
times** what it needs to, on every rasterisation, every boolean, every filter pass and the
output face count — for a surface that is visually indistinguishable from the four-voxel
version once meshed.

The check takes one division. Do it before touching anything else in the settings, and note
that the thinnest feature may not be the nominal wall — trim slivers and lattice
junctions can be thinner, so inspect the actual minimum rather than the design intent.

This rule is host-independent: it applies identically to Dendro's Voxel Size, to Houdini's
VDB from Polygons voxel size, and to Chromodoris's sampling resolution.

## Step 3 — Restructure the pipeline order

| Reordering | Why it helps |
|---|---|
| Trim or intersect **before** smoothing and offsetting | Filter cost is proportional to active voxels × iterations. Filtering the whole lattice and then discarding most of it pays for voxels that are thrown away. |
| Cull trim stubs shorter than about two strut diameters | They add surface area and rasterisation work and contribute nothing after the union. |
| Array a rasterised unit-cell volume instead of arraying curves | Dendro volumes pass through native Array (Box, Linear, Rectangular, Polar) and uniform Scale. Avoids re-rasterising identical geometry thousands of times. Does not apply to graded or conformal lattices. |
| Do smoothing in the volume domain, not the mesh domain | Dendro Smooth Volume does not increase face count. Weaverbird Catmull-Clark multiplies it by 4 per level, on the largest mesh in the definition. |
| Subdivide the unit cell, not the assembly | Divides the subdivision cost by the number of cells. |
| Never mesh and re-voxelise between steps unless deliberately staging resolution | Each round trip pays a full extraction plus a full rasterisation and loses accuracy both ways. |

The staged-resolution exception is real and documented: a configurator developer on the
McNeel forum described creating "the overall form in low resolution, and then for the
buckles, slices, and fit, I go to high resolution ... by converting the low res volume into
a mesh and then voxelizing it to high res." That is a legitimate pattern, but the same
thread notes that large resolution jumps caused failures, so the increments must be modest.

## Step 4 — Change the geometry, not the settings

These are design decisions, so they belong to the engineer rather than to a tuning pass —
but they are the largest levers available.

| Design change | Effect on cost |
|---|---|
| Increase strut radius or wall thickness | Raises the voxel-size floor. Roughly three voxels across a strut radius are needed before it stops looking faceted, so a 50% thicker strut permits a 50% larger voxel and cuts voxel count by more than half. |
| Increase unit cell size | Lattice surface area scales inversely with cell size; area is a linear term in the cost model. |
| Restrict the lattice to the region that structurally needs it | Directly reduces surface area, the dominant term. |
| Avoid shelling where a solid will do | Cost follows boundary area, so a shell costs about twice a solid of the same outer form. |

## Step 5 — Change the approach

For a gyroid or any other TPMS, the largest available speedup is not a setting. It is
skipping the rasterisation entirely by sampling the implicit function directly and meshing
once — Chromodoris Sample Voxels Custom, Jellyfish, or Millipede IsoSurface. See
`08-gyroid-workflow-and-faster-alternatives.md`. Four of the six stages in the conventional
Crystallon-plus-Weaverbird-plus-Dendro gyroid pipeline are not structurally necessary.

## Step 6 — Bypass the component layer

For a production tool rather than an exploratory definition, reference `DendroGH.dll`
directly from a C# component or a compiled plug-in. Laurent Delrieu reports on the McNeel
forum: "I use it like that and it is faster than using Component from Dendro," attributing
the gain to eliminating visualisation overhead.

This also opens the door to OpenVDB's documented parallel-insertion pattern: rasterise
disjoint bundles of lattice curves into separate grids on separate threads and merge, since
"because OpenVDB is sparse and hierarchical, merging is very efficient." The canvas
components do not expose this.

Two rules apply when working at this level: each thread needs its own ValueAccessor, and
every accessor must be cleared after any topology-changing operation such as pruning or CSG.

## Diagnosing an invalid mesh

Invalid meshes out of Volume to Mesh "often appear to output correctly and render from the
Grasshopper canvas without issue but fail on subsequent operations and will not bake."
Hover the M output to see the warning.

Fix order:

1. Raise Isovalue from 0 to 0.001 or 0.002. Documented as the most common fix. Note Dendro
   normalises the isovalue by voxel size, so this is a relative nudge that behaves
   consistently across resolutions.
2. Adjust Adaptivity.
3. Run Pufferfish's Rebuild Mesh with Merge Vertices enabled — reported on the McNeel forum
   as a computationally efficient cleanup for this exact symptom.
4. Check the input mesh is genuinely closed. Mesh to Volume "only works on closed meshes. If
   a mesh is open you will get erratic results."

## What will not help

| Common attempt | Why it does not help |
|---|---|
| Reducing the part's size or enclosed volume | Cost follows boundary surface area, not volume. A smaller part with the same lattice density costs proportionally less only because it holds fewer cells. |
| Raising Adaptivity to fix a slow *volume* operation | Adaptivity only affects mesh extraction output. The level set was already built at full voxel resolution. |
| Increasing smoothing iterations after lowering voxel size | The Laplacian time step is `dx²/6`, so reaching the same physical smoothing radius at half the voxel size needs four times the iterations over four times the voxels — sixteen times the work for the same visual result. |
| Adding RAM alone | Helps only if the definition was paging. The dominant costs are quadratic in voxel size and single-threaded in the Grasshopper half; neither is memory-bound. |
