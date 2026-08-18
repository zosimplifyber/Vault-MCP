# OpenVDB Tools — Parameters, Semantics and Relative Cost

Source: OpenVDB VolumeToMesh.h, https://www.openvdb.org/documentation/doxygen/VolumeToMesh_8h_source.html
Source: OpenVDB LevelSetFilter.h, https://www.openvdb.org/documentation/doxygen/LevelSetFilter_8h_source.html
Source: OpenVDB Overview and FAQ, https://www.openvdb.org/documentation/doxygen/overview.html
Compiled for Simplifyber engineering, August 2026.

These are the OpenVDB tools that Dendro's Grasshopper components call. The mapping from
component to call is in `05-dendro-to-openvdb-call-mapping.md`; this document covers what
each tool does and what it costs.

## Conversion into a volume

| OpenVDB conversion tool | Signature parameters that matter | Cost behaviour of that tool |
|---|---|---|
| `openvdb::tools::meshToVolume` | mesh, transform, exteriorBandWidth, interiorBandWidth, conversionFlags | Band widths are given **in voxel units**, not world units. Cost tracks triangle count plus the number of narrow-band voxels generated. |
| `openvdb::tools::createLevelSet<FloatGrid>(voxelSize, halfWidth)` | voxelSize in world units, halfWidth in voxel units (default 3) | Creates an empty level set with background value `halfWidth * voxelSize`. |
| `openvdb::tools::ParticlesToLevelSet` + `rasterizeSpheres` | particle list supplying position and radius | Cost tracks the swept surface area of the spheres divided by voxel area. |

The band width being in **voxel units** is the single most misread parameter in the whole
stack. It means the physical thickness of the active band shrinks in proportion when you
shrink the voxel size, so the band voxel count grows as the square of the resolution
increase, not the cube. See `03-voxel-cost-model.md` for the arithmetic.

A related documented caveat: it is "generally advisable to specify a half-width of the
narrow band that is larger than one voxel unit, otherwise zero crossings are not
guaranteed." Below roughly two voxels the surface stops being reliably extractable.

## CSG / boolean

`csgUnion`, `csgIntersection` and `csgDifference` all operate in place on the first grid.

The important structural fact: **the two grids must share a transform.** Where they do not,
the caller has to resample one onto the other's index space first, and Dendro does exactly
that using `openvdb::tools::GridTransformer` with `BoxSampler`. A resample is a full sweep
over the source grid's active voxels, so a boolean between two volumes built at different
voxel sizes silently costs an extra full pass that a boolean between two matched volumes
does not.

CSG also changes topology, which invalidates any ValueAccessor held on the grid.

## LevelSetFilter — smoothing and offset

All filter operations run through `LevelSetTracker`, which performs "proper interface
tracking which allows for unrestricted surface deformations" by rebuilding the narrow band
as the surface moves. That tracking is why an offset larger than the band still works, and
also why it costs what it costs.

| LevelSetFilter operation | Documented mechanism | Relative cost of that operation |
|---|---|---|
| Laplacian flow in LevelSetFilter | Time step `dt = dx²/6`. Documented as: "if the grids contains a true signed distance field ... Laplacian diffusion ... is actually identical to mean curvature diffusion, yet less computationally expensive!" | Cheapest correct smoother for a true SDF. |
| Mean-value flow in LevelSetFilter | Separable box filter across the three axes | Fast; separable, so cost is linear in kernel width rather than cubic. |
| Gaussian filter in LevelSetFilter | "approximated as 4 iterations of a separable mean filter which typically leads an approximation that's better than 95%!" | Roughly four times the cost of one mean filter pass. |
| Median-value flow in LevelSetFilter | Non-separable, dense stencil computing local medians | Documented as "relatively slow"; the most expensive of the four. |
| Mean-curvature flow in LevelSetFilter | Time step `dt = dx²/3`; "parabolic mean-curvature diffusion" | "Computationally expensive but geometrically accurate." Laplacian gives the same result more cheaply on a true SDF. |
| Offset in LevelSetFilter | Constant value shift under CFL-limited time stepping, `CFL = 0.5 × voxelSize`; the loop continues while `offset - dist > 0.001 × CFL` | Iteration count scales as roughly `2 × distance / voxelSize`. Halving voxel size doubles the number of sweeps *on top of* quadrupling the voxels swept. |
| Fillet in LevelSetFilter | Offsets only where principal curvature is negative, proportionally to its magnitude | Converges toward the convex hull with more iterations. |

The two diffusion time steps are the basis of a rule that is easy to miss: to reach a
**fixed physical** smoothing radius R, the iteration count needed scales as (R/dx)². So
halving the voxel size costs four times the voxels and four times the iterations — sixteen
times the smoothing work for visually the same result.

### The alpha mask

Filters accept an optional scalar mask field. Values below the mask minimum map to alpha 0,
values above the maximum map to alpha 1, and intermediate values interpolate smoothly.
Inverting reverses the mapping. This is what Dendro's Create Mask component drives, and it
is the mechanism for confining an expensive filter to a small region instead of running it
over every active voxel in the model.

## volumeToMesh

`volumeToMesh(grid, points, triangles, quads, isovalue, adaptivity)`.

| volumeToMesh parameter | Meaning of that parameter | Effect of that parameter on output and cost |
|---|---|---|
| `isovalue` in volumeToMesh | "determines which isosurface to mesh", default 0.0 | For a level set, 0.0 is the true surface. Nudging it acts as a small offset. |
| `adaptivity` in volumeToMesh | Threshold in the range 0 to 1 | "higher thresholds will allow more variation in polygon size, using fewer polygons to express the surface." At 0 the mesh is uniform; as it rises, flat regions get larger polygons while curved regions keep detail. |

Output topology depends on which path is taken: the uniform meshing function "produces only
quads", while the adaptive variant produces both quads and triangles, since "triangles will
only be created for areas of the mesh which hit the adaptivity threshold and can't be
represented as quads." If pure triangles are required, the documentation advises
triangulating the quad output afterwards rather than forcing adaptive meshing.

Meshing is threaded — the implementation uses Intel TBB to work across leaf nodes in
parallel. For reference-based meshing of fractured SDF fragments the documented overhead is
"approximately 15% for the first fragment and neglect-able for subsequent fragments."

**Adaptivity does not make the volume operations faster.** It reduces the polygon count
coming out of the volume, which speeds up everything downstream — Grasshopper display,
Weaverbird, baking, STL export — but the level set that produced it was already built at
full voxel resolution.

## Diagnostics

OpenVDB ships `CheckLevelSet` and `CheckFogVolume`, which run a battery of tests on
symmetric narrow-band level sets and fog volumes respectively. These are the tools to reach
for when a grid produces an invalid mesh rather than guessing at isovalue tweaks. They are
not exposed by Dendro's Grasshopper components.
