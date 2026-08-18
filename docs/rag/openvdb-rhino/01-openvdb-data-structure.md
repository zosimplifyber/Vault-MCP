# OpenVDB Data Structure — What Actually Costs Time and Memory

Source: OpenVDB Overview, https://www.openvdb.org/documentation/doxygen/overview.html
Source: OpenVDB FAQ, https://www.openvdb.org/documentation/doxygen/faq.html
Compiled for Simplifyber engineering, August 2026.

OpenVDB is the sparse volumetric library underneath Dendro in Grasshopper and underneath
Rhino 8's own ShrinkWrap. Understanding what it stores is the whole basis of making a
Rhino volumetric workflow fast, so this document covers the structure before any tuning
advice.

## The tree

OpenVDB uses a B-tree-like acceleration structure with three node types over four levels:
a RootNode, two InternalNode levels, and a LeafNode level. Quoting the overview: "the
RootNode and InternalNodes increasingly subdivide the three-dimensional index space, and
the LeafNodes hold the actual unique voxels."

The default branching factors are 5, 4, 3, given as base-two logarithms read from leaf up
to root:

| Node level in the default OpenVDB tree | Extent of that node level in the default OpenVDB tree |
|---|---|
| LeafNode in the default OpenVDB tree | 2³ = 8×8×8 voxels; this is where unique per-voxel values live |
| Level-1 InternalNode in the default OpenVDB tree | 2⁴ = 16×16×16 children, each child being an 8³ leaf block |
| Level-2 InternalNode in the default OpenVDB tree | 2⁵ = 32×32×32 children; each level-2 node subsumes 4096³ voxels |
| RootNode in the default OpenVDB tree | Unrestricted child count, indexed by 32-bit signed integers |

The practical consequence: **allocation happens in 8×8×8 leaf blocks.** A grid does not
pay for empty space, but it does round up to whole leaf blocks around anything it does
store. A thin surface threading through space still allocates a full 512-voxel leaf
wherever it passes.

## Three ways a value is stored

| Value storage form in an OpenVDB grid | What that storage form means for cost |
|---|---|
| Voxel value in an OpenVDB grid | A unique value stored in a LeafNode. This is the only form that costs per-voxel memory. |
| Tile value in an OpenVDB grid | One uniform value standing in for an entire node subtree. Costs almost nothing regardless of the volume it covers. |
| Background value in an OpenVDB grid | The value returned for any coordinate the tree does not resolve. Costs nothing at all. |

## Active versus inactive

Every voxel and tile carries an active/inactive flag marking "interesting" data versus
background. The overview's own example is the one that matters here: "voxels used to store
the distance values of a narrow-band level set ... will be marked as active while the other
('far') voxel locations will be marked as inactive."

**Active voxel count is the number to reason about when a Dendro definition is slow.** It
is reported per grid in the file metadata alongside the index-space bounding box and the
memory usage in bytes.

## Narrow-band level set versus fog volume

| Volume type in OpenVDB | Interior region | Band | Exterior region |
|---|---|---|---|
| Narrow-band level set in OpenVDB | Inactive voxels holding a constant negative distance | Active voxels holding signed distance, "normally three voxels wide on either side of the surface" | Inactive voxels holding a constant positive distance |
| Fog volume in OpenVDB | Active voxels with value one | Active voxels interpolating linearly between zero and one | Inactive voxels with value zero |

Dendro works in narrow-band level sets throughout. That is why its cost tracks **surface
area**, not enclosed volume: only the shell near the zero crossing is ever active. A solid
block and a hollow shell of the same outer dimensions cost roughly the same in OpenVDB.

## ValueAccessor

Random `getValue(coord)` on a grid walks the tree from the root every time. The recommended
pattern is a ValueAccessor, "an accelerator object that performs bottom-up tree traversal
using cached information from previous traversals." It records the node sequence from the
most recent access and, on the next access, "performs an inverted traversal from the
deepest recorded node up."

The documented speedup is that "a factor of three is typical."

Two rules attach to it:

1. **Caching is not thread-safe.** A grid may hold many accessors, so "each thread can
   safely be assigned its own value accessor."
2. **Accessors must be cleared after any topology change** — "after any operation that
   removes nodes from the grid's tree, such as pruning, CSG or compositing."

This matters for anyone writing a C# or C++ component against Dendro's API rather than
using the canvas components.

## Multithreading model

OpenVDB's recommended pattern for parallel insertion is per-thread grids merged at the end:
"we typically assign a separate grid to each thread and then merge the grids as threads
terminate. This technique works remarkably well: because OpenVDB is sparse and
hierarchical, merging is very efficient."

This is the relevant model for rasterising a large lattice: N curve bundles can be
rasterised into N grids in parallel and merged, rather than serialised into one grid.

## Why OpenVDB can beat a dense grid even on dense data

From the FAQ: OpenVDB gives "fast data access (both random and sequential)" and can help
even dense workloads through "improved CPU cache performance due to its underlying blocking
and hierarchical tree structure." The 8³ leaf block is a cache-friendly unit.

OpenVDB is also not a level-set-only library — it was "developed for general-purpose
volumetric processing and numerical simulation." Narrow-band level sets are simply the
part of it Dendro exposes.
