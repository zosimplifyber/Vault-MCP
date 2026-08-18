# Porting the Crystallon / Dendro Workflow to Houdini

Source: SideFX Houdini node reference — VDB from Polygons, VDB from Particles, Lattice from Volume, Lattice Deform, Point Deform, Volume Wrangle, https://www.sidefx.com/docs/houdini/nodes/sop/
Source: Official Crystallon documentation, 75 pp (component behaviour being mapped)
Source: cgwiki HoudiniVolumes, https://www.tokeru.com/cgwiki/HoudiniVolumes.html
Compiled for Simplifyber engineering, August 2026.

## The short version

Everything Crystallon does can be rebuilt in Houdini, and everything **Dendro** does is
already native there — Houdini ships the OpenVDB SOPs, so the entire volumetric half of the
workflow stops being a plug-in and becomes first-class, multithreaded, and considerably
richer.

The two genuine gaps are Crystallon's **LTCX and INP exporters** and its **preset unit cell
library**. Neither is hard to reproduce; both are work.

## Why the volumetric half is the easy part

Dendro is a wrapper around OpenVDB. Houdini embeds the same library directly, with a
one-to-one correspondence between the Dendro components and the Houdini SOPs:

| Dendro component | Houdini SOP | Underlying OpenVDB call, shared by both |
|---|---|---|
| Mesh to Volume (Dendro) | VDB from Polygons | `tools::meshToVolume` |
| Curve to Volume / Points To Volume (Dendro) | VDB from Particles | `tools::ParticlesToLevelSet::rasterizeSpheres` |
| Volume Union / Intersection / Difference (Dendro) | VDB Combine, SDF Union / Intersection / Difference | `tools::csgUnion` / `csgIntersection` / `csgDifference` |
| Smooth Volume (Dendro) | VDB Smooth SDF | `tools::LevelSetFilter` gaussian / mean / median / laplacian |
| Offset Volume (Dendro) | VDB Reshape SDF | `LevelSetFilter::offset` |
| Volume Blend (Dendro) | VDB Morph SDF | `tools::LevelSetMorphing` |
| Volume to Mesh (Dendro) | Convert VDB, to Polygons | `tools::volumeToMesh` with isovalue and adaptivity |
| Write Volume / Read Volume (Dendro) | Native `.vdb` File SOP read and write | — |
| Create Mask (Dendro) | Any VDB fed to a SOP's alpha-mask input | `LevelSetFilter` alpha mask |

The cost model in `03-voxel-cost-model.md` transfers unchanged, because it is a property of
OpenVDB rather than of either host. Voxel size, band width and surface area behave
identically. What changes is that Houdini exposes more of the library — VDB Resample, VDB
Activate, VDB Clip, VDB Advect SDF, VDB LOD — and does not run the graph on a single
solver thread.

**`pscale` is the important idiom.** VDB from Particles reads a per-point `pscale` attribute
as the sphere radius, so Crystallon's Lattice Thickness components — which output nothing
but a list of one thickness value per curve — become a plain point attribute. Graded strut
thickness stops being a special feature and becomes an attribute you write in VEX. Note the
documented floor: "if points are smaller than 1.5 voxels, they cannot be resolved and will
not appear in the VDB", and the Minimum Radius in Voxels parameter defaults accordingly.

## Mapping Crystallon's logic

### Voxelize — building the container cells

Crystallon's TwistedBox cells have a close Houdini analogue in **Lattice from Volume**, which
creates "a point cloud, connected polyline, tet or hex mesh around the active region of
volumes." With Output Type set to Hexahedron it produces exactly what Crystallon's Voxelize
Distance produces — a hex cell per grid location over the occupied region — plus `ix`, `iy`,
`iz` integer attributes giving each cell's position in the 3D grid, and a `rest` attribute
holding original point positions.

Those grid attributes are more than Crystallon offers, and they are what makes graded and
tweened lattices straightforward.

| Crystallon Voxelize component | Houdini approach |
|---|---|
| Voxelize Distance (Vd) | VDB from Polygons (SDF) → Lattice from Volume, Output Type Hexahedron. Or Points from Volume → Copy to Points with a box. |
| Voxelize Parameter (Vp) — varying sized voxels | Build the cell grid from non-uniform division lists in a Wrangle, or deform a uniform grid by a ramp |
| Morph Between Surfaces (MBS) / Morph Between Meshes (MBM) | Point Deform, or Lattice Deform; or build cells in rest/UV space and deform to the target |
| Conformal Rib / Pipe, Distance and Parameter (CRd, CRp, CPd, CPp) | Resample the curve, orient with Polyframe, then Copy to Points |
| Mesh Offset Parameter / Value / Attractor (MOp, MOv, MOa) | Peak or PolyExtrude along normals with a per-point offset attribute, then build cells between the two surfaces |
| Voxel Morph Value / Attractor (VMv, VMa) | Attribute from Volume, Attribute Transfer, or a Point Wrangle using `xyzdist` / `nearpoint` |

### Populate — mapping a unit cell into each cell

Crystallon's Cell Fill performs a trilinear map of a unit cell into each TwistedBox. In
Houdini:

- **Uniform lattices** — Copy to Points, which "copies geometry in the first input onto the
  points of the second input". Fastest path, and it handles instancing.
- **Graded or conformal lattices** — Lattice Deform or Point Deform. Point Deform is the
  better default: it "deforms geometry according to a point cloud" using connectivity to
  deduce local transforms, which "avoids the collapsing that can occur with the Lattice
  SOP's point mode when the transformation mesh rotates." That collapsing is precisely the
  failure mode a twisted, conformal lattice provokes.

| Crystallon Populate component | Houdini approach |
|---|---|
| Cell Type (CT) / Cell Selector (CS) | Any geometry input; a Switch SOP or an HDA menu parameter. The preset library has to be rebuilt or imported. |
| Cell Fill (CF) / Cell Shell Fill (CSF) | Copy to Points for uniform; Point Deform for graded |
| Tween Cell Fill (TCF) / Tween Shell Fill (TSF) | Blend Shapes SOP, or a VEX `lerp` between two point sets of matching topology — the same topology requirement Crystallon imposes |
| Cell Morph Value (CMv) / Attractor (CMa) | Attribute from Volume or a Wrangle writing a 0–1 attribute per cell |
| Voronoi Fill (VF) | Voronoi Fracture Points and Voronoi Fracture |

### Modify — the cleanup group

This is where Houdini wins most decisively, because Crystallon's own documentation flags
these as slow: Remove by Valence carries the note "(this can be very slow, sorry)", and
Remove Floating and Remove Short both advise restricting them to the Trimmed Lattice output
"to reduce time".

| Crystallon Modify component | Houdini equivalent |
|---|---|
| Trim Lattice (TL) | VDB from Polygons (SDF) → Attribute from Volume to sample the SDF onto points → Blast. Or Boolean in Shatter mode. **There is no two-output trap** — nothing is silently discarded. |
| Trim Shell (TS) | Boolean, or VDB Combine SDF Intersection |
| Remove Floating (RF) | Connectivity SOP → Blast on piece size |
| Remove Short (RS) | Measure (perimeter) → Blast on threshold |
| Remove by Valence (RV) | Group SOP or Wrangle using `neighbourcount(0, @ptnum)` — near-instant |
| Remove Duplicate Curves (RD) | Fuse SOP. Note Crystallon deliberately approximates this by comparing midpoints only, for speed; Houdini does not need the shortcut. |
| Merge Curves (MC) | Fuse |
| Lattice Connections (LC) | Connect Adjacent Pieces |
| Morph Lattice / Shell to Skin (MLS, MSS) | Ray SOP to project onto the skin, or Point Deform |

### Thicken and export

| Crystallon component | Houdini equivalent |
|---|---|
| Lattice Thickness Value / Attractor | Write `@pscale` in a Point Wrangle. VDB from Particles consumes it directly. |
| Shell Thickness Value / Attractor | Write a per-point offset attribute, consumed by Peak or PolyExtrude |
| Lattice Topology (LT) | Native point/primitive topology; `neighbours()` in VEX |
| **LTCX Writer / Reader** | **No equivalent.** A Python SOP writing the beam-graph text format — nodes, connections, radii. |
| **INP writers (B32, S3, S4, C3D4, C3D6, C3D8)** | **No equivalent.** A Python SOP per element type, or use Houdini's own FEM/Vellum solvers and skip Abaqus entirely. |

## The gyroid case collapses to three nodes

This is the largest single simplification, and it removes the entire Crystallon-plus-
Weaverbird front end. A gyroid is an implicit function, so in Houdini it is written directly
into the field rather than built from geometry:

```c
// Volume Wrangle, running over an SDF VDB covering the part
float s = 2 * $PI / chf("cell_size");
float g = sin(@P.x*s)*cos(@P.y*s)
        + sin(@P.y*s)*cos(@P.z*s)
        + sin(@P.z*s)*cos(@P.x*s);
@surface = abs(g) - chf("thickness");   // sheet gyroid of finite thickness
```

Then VDB Combine (SDF Intersection) against the part's SDF, then Convert VDB to Polygons.

Compare against the Grasshopper route documented in
`08-gyroid-workflow-and-faster-alternatives.md`: cell generation, thickening, subdivision
and rasterisation all disappear. There is no intermediate polygon mesh at all, so there is
nothing to explode to 26 million faces. Volume Wrangle is threaded, and grading the cell
size or thickness is a matter of making `s` or `thickness` a function of `@P` or of a
sampled attribute.

## What actually transfers, and what does not

| Consideration | Assessment |
|---|---|
| The volumetric operations | Native, threaded, and a superset of Dendro. Direct transfer. |
| The cost model in this collection | Transfers unchanged — it is a property of OpenVDB, not of the host. |
| Crystallon's cell-mapping logic | Reproducible with Copy to Points and Point Deform, with better handling of rotation-heavy conformal cases |
| Crystallon's cleanup tools | Faster by a wide margin, and without the documented slow paths |
| Crystallon's preset unit cell library | Must be rebuilt, or exported from Grasshopper as mesh/curve files and imported once |
| LTCX and INP export | Must be written. Both are text formats; a Python SOP each. |
| NURBS and Brep fidelity | Houdini is a polygon and volume application. A Rhino Brep has to be meshed on the way in. Fine for lattice infill; not a substitute where exact Brep geometry is required downstream. |
| The interactive design loop | A real cost. SOP networks and VEX are not Grasshopper, and the person driving the design has to learn them. |

## The bridge that needs no porting at all

Dendro has **Write VDB**, and Crystallon's documentation cites `.vdb` interchange with
Houdini and Maya as a reason it exists. So the split is available today without rewriting
anything:

```
Rhino / Grasshopper                 Houdini
  Crystallon + thickness values
        ↓
  Dendro Curve to Volume  →  .vdb  →  File SOP
                                        ↓
                                     VDB Combine / Smooth SDF / Reshape SDF
                                        ↓
                                     Convert VDB → mesh or .vdb back out
```

This keeps the design intent in Grasshopper where the designer works and moves the
expensive volumetric operations to the application that does them natively and in parallel.

## Recommended order

1. **Fix the Grasshopper definition first.** The changes in `09-performance-playbook.md` are
   hours of work and keep the existing workflow intact.
2. **Use the `.vdb` bridge** for anything that is still heavy after that. No porting, and it
   is reversible.
3. **Port properly only if lattice work is recurring** rather than a one-off. Start with the
   gyroid case, because it is three nodes and demonstrates the whole argument.
4. **Budget separately for LTCX and INP** if the downstream FEA or nTopology handoff matters.
   These are the only pieces with no equivalent, and discovering that late would be
   unpleasant.
