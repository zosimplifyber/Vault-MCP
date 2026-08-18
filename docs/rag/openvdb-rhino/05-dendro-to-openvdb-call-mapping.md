# What Each Dendro Component Actually Calls in OpenVDB

Source: `DendroAPI/DendroGrid.cpp`, https://github.com/ryein/dendro/blob/master/DendroAPI/DendroGrid.cpp
Cross-referenced against the OpenVDB tool documentation cited in `02-openvdb-tools-and-their-cost.md`.
Compiled for Simplifyber engineering, August 2026.

Dendro's Grasshopper components are thin wrappers. Knowing the exact OpenVDB call behind
each one turns vague tuning advice into arithmetic, because the OpenVDB documentation for
those calls specifies units, time steps and CFL limits that the Dendro documentation does
not.

## The mapping

| Dendro component | OpenVDB call in DendroGrid.cpp | Consequence for tuning |
|---|---|---|
| Mesh to Volume (Dendro) | `openvdb::tools::meshToVolume<openvdb::FloatGrid>(mesh, xform, bandwidth, bandwidth, 0, NULL)` with `xform.preScale(voxelSize)` | Dendro passes the **same** value as both exteriorBandWidth and interiorBandWidth, so the band is symmetric. Both are in **voxel units**, so the physical band thickness is `bandwidth × voxelSize`. |
| Points To Volume (Dendro) | `openvdb::tools::ParticlesToLevelSet<openvdb::FloatGrid>` with `raster.rasterizeSpheres(vPoints)`, grid from `openvdb::createLevelSet<openvdb::FloatGrid>(voxelSize, bandwidth)`, transform from `openvdb::math::Transform::createLinearTransform(voxelSize)` | Cost tracks the swept sphere surface area. A per-point radius list is rasterised sphere by sphere. |
| Curve To Volume (Dendro) | Same sphere-rasterisation path as Points To Volume — the curve is discretised and wrapped with a spherical profile | This is why a lattice of many short curves costs what it costs: every curve contributes `2πrL` of surface to the band. |
| Volume Union (Dendro) | `openvdb::tools::csgUnion(*mGrid, *cGrid, true)` | In-place on the first grid. |
| Volume Intersection (Dendro) | `openvdb::tools::csgIntersection(*mGrid, *cGrid, true)` | In-place on the first grid. |
| Volume Difference (Dendro) | `openvdb::tools::csgDifference(*mGrid, *cGrid, true)` | In-place on the first grid. |
| All three booleans (Dendro) | `openvdb::tools::GridTransformer` with `BoxSampler` for resampling between coordinate systems | **Mismatched voxel sizes trigger a full resample of one grid before the boolean runs.** This is the hidden cost of using more than one Create Settings component. |
| Smooth Volume, Type 0 (Dendro) | `filter.gaussian(width)` on `openvdb::tools::LevelSetFilter<openvdb::FloatGrid>` | Internally four iterations of a separable mean filter. |
| Smooth Volume, Type 1 (Dendro) | `filter.laplacian()` | Takes no width, which is why the Dendro documentation says Width has no effect on Laplacian. On a true SDF this is identical to mean-curvature diffusion and cheaper. |
| Smooth Volume, Type 2 (Dendro) | `filter.mean(width)` | Separable box filter across three axes. |
| Smooth Volume, Type 3 (Dendro) | `filter.median(width)` | Non-separable dense stencil, documented by OpenVDB as relatively slow. |
| Offset Volume (Dendro) | `filter.offset((float)amount)` with optional mask support and range constraints | CFL-limited at `0.5 × voxelSize` per step, so iterations scale as `2 × distance / voxelSize`. |
| Volume Blend (Dendro) | `openvdb::tools::LevelSetMorphing<openvdb::FloatGrid> morph(*mGrid, *bGrid.Grid())` then `morph.advect(bStart, bEnd)` with configurable spatial and temporal schemes | The End Time input is the advection end time, which is why the Dendro docs say it "is tied to voxel size." |
| Volume to Mesh (Dendro) | `openvdb::tools::volumeToMesh<openvdb::FloatGrid>(*mGrid, points, triangles, quads, isovalue, adaptivity)` | **Isovalue is normalised by voxel size before the call.** The secondary `VolumeToMesh` class path uses iso-value 0.0 for level sets or 0.01 for fog volumes. |
| Create Mask (Dendro) | The LevelSetFilter alpha-mask mechanism, with min/max defining the alpha ramp and an invert flag | Confines the filter to a region; the rest of the narrow band is skipped. |

## The three findings that come out of this mapping

### 1. Bandwidth is in voxel units, and Dendro applies it symmetrically

`meshToVolume(mesh, xform, bandwidth, bandwidth, 0, NULL)` passes the value twice. So a
Bandwidth of 3 means three voxels outside and three inside — a band six voxels thick — and
that thickness is measured in voxels, so it shrinks physically as the voxel size shrinks.

This is why the active voxel count grows as the square of the resolution increase rather
than the cube. It also means Bandwidth is a straight linear multiplier on cost that most
definitions never touch.

The lower bound: OpenVDB documents that a half-width below one voxel does not guarantee
zero crossings. Two is the practical floor.

The upper bound is set by what the downstream operations need. An Offset and a Smooth both
move the interface, and although `LevelSetTracker` rebuilds the band as it goes, starting
with a band too thin for the intended motion means more rebuild work.

### 2. Mismatched Create Settings costs a full resample per boolean

The presence of `GridTransformer` and `BoxSampler` in the boolean path is the mechanical
reason behind the Dendro documentation's advice to use one Settings component everywhere.
Two volumes built at different voxel sizes cannot be CSG'd directly; one gets resampled
onto the other's index space first, and that resample is a full sweep over its active
voxels.

In a lattice workflow this is easy to hit accidentally: the lattice volume gets a fine
voxel size because the struts are thin, the enclosing part volume gets a coarse one because
it is a smooth shell, and then the trim intersection pays for a resample of the entire part
at the fine resolution.

### 3. Isovalue is normalised by voxel size

Because Dendro divides the isovalue by the voxel size before calling `volumeToMesh`, the
documented "set Isovalue to 0.002" fix for invalid meshes is a **relative** nudge, not an
absolute distance. It behaves consistently as voxel size changes, which is convenient, but
it also means the resulting physical offset of the meshed surface scales with the voxel
size.

## Calling Dendro without the components

The DendroGH assembly can be referenced directly from a Grasshopper C# component or a
compiled plug-in. On the McNeel forum, Laurent Delrieu reports doing exactly this: "I use it
like that and it is faster than using Component from Dendro," attributing the gain to
eliminating the visualisation overhead of the canvas components.

The mechanics: recompile from the GitHub repository to get `DendroGH.dll` and add it as a
project dependency. A `.gha` is a renamed `.dll`, so renaming `dendrogh.gha` to
`dendrogh.dll` makes it referenceable from Visual Studio, though recompiling from source is
the recommended route. Boolean operations are callable directly through the Volume classes.

There is no confirmed shortcut to Rhino's internal OpenVDB — developers building against
Dendro are expected to build OpenVDB and its dependencies (TBB, Boost, Blosc) through the
supplied vcpkg manifest. See `10-rhino8-openvdb-and-environment.md` for why sharing Rhino's
copy is a problem rather than an opportunity.
