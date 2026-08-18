# Crystallon — Lattice Generation Upstream of Dendro

Source: CRYSTALLON Lattice structure tools for Grasshopper3D, official documentation, 75 pp
Source: Crystallon repository, https://github.com/GHCrystallon/Crystallon
Source: FATHOM introduction, https://fathommfg.com/blog/introducing-crystallon/
Source: McNeel forum, "Problem with creating a lattice infill using crystallon with dendro", https://discourse.mcneel.com/t/problem-with-creating-a-lattice-infill-using-crystallon-with-dendro/156184
Compiled for Simplifyber engineering, August 2026.

Crystallon is "an open source project for creating lattice structures using Rhino and
Grasshopper3D", originally developed at FATHOM by Aaron Porterfield. Licensed GPL-3.0.

## Two corrections that change how this plug-in should be understood

### Crystallon's "voxel" is not OpenVDB's voxel

Crystallon has a whole tool group called **Voxelize**, and this causes a persistent
misunderstanding. Its own glossary defines a voxel as "an array of elements of volume that
constitute a notional three-dimensional space, which a representation of a three-dimensional
object is divided."

In Crystallon that means a **container cell to be populated with a unit cell**. The output
type of every Voxelize component is `twistedbox` — a Grasshopper TwistedBox, an eight-corner
deformable box used for trilinear box mapping. The documented output text is identical
across the whole group: `Voxels (V) - [twistedbox] - Voxels to be filled with unit cells`.

A Crystallon voxel therefore carries **no sample value**. There is no signed distance, no
density, no occupancy, no narrow band. It is a deformable cage, and its whole purpose is to
be handed to a Cell Fill component that maps a unit cell into it.

| Meaning of "voxel" | In Crystallon | In OpenVDB / Dendro |
|---|---|---|
| What one voxel is | A TwistedBox container cell, deformable, arbitrary size per instance | A cubic sample location on a regular grid, uniform size across the grid |
| What it stores | Nothing — it is a cage | A signed distance value, plus an active/inactive flag |
| How many there are | One per lattice unit cell, typically thousands | One per grid location in the narrow band, typically millions |
| What consumes it | Cell Fill, producing curves or a mesh | CSG, filters, volumeToMesh |
| Can it be written to a file | No | Yes, `.vdb`, via Dendro Write Volume |

### Crystallon does not generate gyroids

The word "gyroid" appears **zero times** in the 75 pages of official Crystallon
documentation. So does "TPMS". The Cell Type component is a library of beam and shell unit
cells — Lattice Cell, Lattice Shell, Lattice Hinge Cell, Lattice Hinge Shell, Mesh Cell,
Mesh Shell — all of which are populated into TwistedBox voxels.

Tutorials that describe "generating a gyroid lattice using the Crystallon plugin" are
conflating Crystallon's cell-fill machinery with an isosurface step performed elsewhere. A
gyroid is a TPMS defined by an implicit function and has to come from an isosurface tool
(Chromodoris, Jellyfish, Millipede IsoSurface) or from a hand-built unit cell that
approximates it. See `08-gyroid-workflow-and-faster-alternatives.md`.

## Architecture, and why it matters for speed

Crystallon is **not compiled components**. Every tool "is left as a cluster which can be
opened and modified at will, in the spirit of open source", built "using only native
Grasshopper components". The distributed files are `.ghuser` user objects.

A Crystallon lattice is therefore produced by interpreted Grasshopper component graphs, not
by native code, and that cost is paid **before Dendro is reached at all**, single-threaded on
the Grasshopper solver thread. When a lattice definition is slow, time the Crystallon stage
and the Dendro stage separately before tuning either; they have opposite remedies and
opposite hardware preferences.

The documentation's own performance notes corroborate this. Remove by Valence carries the
aside "(this can be very slow, sorry)". Remove Floating and Remove Short both say "To reduce
time, use this tool on only the 'Trimmed Lattice' output of the Trim Lattice tool."

## The component groups

### Voxelize — build the container cells

All outputs are `twistedbox` unless noted.

| Crystallon Voxelize component | What it does |
|---|---|
| Voxelize Distance (Vd) | Fill a volume with equal sized voxels. Inputs geometry, base plane, voxel size X/Y/Z, and Fill Completely — if true voxels fill the entire volume, if false only voxels with their centroid inside are kept. |
| Voxelize Parameter (Vp) | Fill a volume with varying sized voxels, driven by 0–1 division ranges per axis. |
| Morph Between Surfaces (MBS) | Conformal fill of voxels between two or more surfaces. |
| Morph Between Meshes (MBM) | Create voxels between the faces of meshes with matching topology. Also outputs a quad mesh. |
| Voxel Morph Value (VMv) | Morph voxels with a point cloud and corresponding values. |
| Voxel Morph Attractor (VMa) | Morph voxels with attractors. |
| Mesh Offset Parameter (MOp) | Offset a mesh uniformly into voxels. Quad meshes give one voxel per face; triangulated meshes are subdivided first. Also outputs the quad mesh. |
| Mesh Offset Value (MOv) | Offset mesh faces into voxels using a point cloud and values. |
| Mesh Offset Attractor (MOa) | Offset mesh faces into voxels using attractors. |
| Surface Offset Value (SOv) | Offset a surface using a point cloud and values. Outputs a `surface`, not voxels. |
| Surface Offset Attractor (SOa) | Offset a surface using attractors. Outputs a `surface`. |
| Conformal Rib Distance (CRd) | A row of equal length voxels along a curve on a surface. |
| Conformal Rib Parameter (CRp) | A row of varying length voxels along a curve on a surface. |
| Conformal Pipe Distance (CPd) | A pipe of equal length voxels along a curve on a surface. |
| Conformal Pipe Parameter (CPp) | A pipe of varying length and size voxels along a curve on a surface. |

### Populate — turn voxels into geometry

**This is where Crystallon stops being volumetric.** Every Populate output is curves or mesh.

| Crystallon Populate component | Output type |
|---|---|
| Cell Type (CT) | `Unit Cell (U) - [geometry]`. A library of pre-made unit cells — Lattice Cell, Lattice Shell, Lattice Hinge Cell, Lattice Hinge Shell, Mesh Cell, Mesh Shell — also usable as reference for building your own. |
| Cell Selector (CS) | `Unit Cell Selector (US) - [curves / mesh]`, chosen from a drop-down. |
| Cell Fill (CF) | `Lattice (L) - [curves]` — the list of curves that make up the lattice. |
| Cell Shell Fill (CSF) | `Lattice (shell) (L(s)) - [mesh]` — a joined mesh. Takes a merge tolerance around 0.1 to 0.001. |
| Tween Cell Fill (TCF) | `Lattice (L) - [curves]`. Morphs between two unit cells by a 0–1 value per voxel; the two cells must share topology. |
| Tween Shell Fill (TSF) | `Lattice (shell) (L(s)) - [mesh]`. |
| Cell Morph Value (CMv) | `Values (Va) - [number]`, one 0–1 value per voxel. |
| Cell Morph Attractor (CMa) | `Values (Va) - [number]`, one 0–1 value per voxel. |
| Voronoi Fill (VF) | Voronoi lattice and skin as `curves`. Stochastic fill with attractor-driven density. |

### Modify — clean up the curve network

| Crystallon Modify component | Notes |
|---|---|
| Trim Lattice (TL) | Trims lattice curves to within a closed brep or mesh. **Two outputs** — see the trap below. |
| Trim Shell (TS) | Trims a shell by removing mesh vertices outside a closed volume. Outputs `mesh`. |
| Remove Floating (RF) | Removes curves with no connection to others. Documented advice: run on the Trimmed Lattice output only, to reduce time. |
| Remove Short (RS) | Removes curves below a length. Same time-saving advice. |
| Remove by Valence (RV) | Removes curves by connection count. Documented as "this can be very slow, sorry". If only removing valence 1, the Untrimmed Lattice output already covers it. |
| Remove Duplicate Curves (RD) | Removes curves sharing a midpoint — deliberately a fast approximation rather than a full comparison. |
| Merge Curves (MC) | Merges curve endpoints by distance. |
| Lattice Connections (LC) | Adds struts between the closest nodes of two adjacent lattices. |
| Morph Lattice to Skin (MLS) | Snaps lattice nodes within a distance to the nearest skin node. |
| Morph Shell to Skin (MSS) | Snaps shell vertices within a distance to the closest point on the skin. |

### Thicken — produce values, not geometry

All four Thicken components output `Values (Va) - [number]` only: one thickness value per
lattice curve, or one per shell vertex. **Crystallon does not thicken anything itself.** The
values are meant to be fed to a downstream thickener — the Shell Thickness documentation
names Weaverbird's Mesh Thicken explicitly, and the per-curve values are what feed Dendro's
Curve to Volume radius input.

This is the actual handoff point in the pipeline, and it is a list of numbers.

### Utilities — the export components

| Crystallon export component | Output | Format |
|---|---|---|
| LTCX Writer (LXO) | `Output (O) - [text]` — "copy and paste contents to a text editor and save with *.ltcx extension" | LTCX, a beam-based lattice interchange format (nTopology). Takes lattice curves and a node radius list. |
| LTCX Reader (LXI) | Nodes, node radii, line-point connections | Reads LTCX back in. |
| INP B32 | `text`, saved as `*.inp` | Abaqus beam elements from a lattice. |
| INP S3 / S4 | `text`, saved as `*.inp` | Abaqus shell elements from a triangle or quad shell. |
| INP C3D6 / C3D8 | `text`, saved as `*.inp` | Abaqus solid elements from a triangulated or quad mesh. |
| INP C3D4 | `text`, saved as `*.inp` | Abaqus tetrahedral elements. Requires Tetrino. |
| Lattice Topology (LT) | Nodes, line-point and point-line connections | A clean graph of unique nodes and their connections. |

**None of these is a volumetric format.** LTCX is a beam graph — nodes, connections and
radii. INP is an FEA mesh. There is no density grid, no signed distance field, and no
`.vdb`.

## The Trim Lattice two-output trap

The most consequential known failure in the Crystallon-to-Dendro handoff, and it produces a
wrong result rather than an error.

The documented outputs are:

- `Trimmed Lattice (L) - [curves] - Lattice curves that have been trimmed`
- `Untrimmed Lattice (L) - [curves] - Lattice curves that have not been trimmed (Typically removes curves with valence 1)`

The second output is the lattice **interior** — every beam that lay wholly inside the trim
volume and so needed no trimming. On any real part that is the large majority of the beams.

On the McNeel forum a user reported Dendro "only thickening the wireframe at the surface of
the model, thus making it hollow and not well connected inside." Aaron Porterfield's
instruction: "You should connect both the outputs to the dendro Curve to Volume component."
The user confirmed: "After connecting the untrimmed lattice to curve to volume it worked!"

Note the performance consequence. A definition wired to only the first output meshes a thin
boundary shell and is therefore *much faster* while being wrong. Fixing it makes the
definition slower, so any benchmark taken before the fix is meaningless.

## Getting a genuine voxel representation out of Crystallon

There is no direct route. The path is through Dendro, which Crystallon's own
Dependencies page lists for exactly this purpose, quoting Dendro's description of wrapping
"points, curves, and meshes as a volumetric data type".

```
Crystallon Cell Fill        →  curves
  + Lattice Thickness       →  one radius per curve
        ↓
Dendro Curve to Volume      →  Dendro Volume (an OpenVDB narrow-band level set)
        ↓
Dendro Write Volume         →  .vdb file
```

For the shell path, `Cell Shell Fill` produces a mesh, which goes through `Mesh to Volume`
instead — but note that Mesh to Volume requires a **closed** mesh, and a shell lattice
trimmed by Trim Shell will not be closed at the trim boundary.

Crystallon's other listed dependencies are Kangaroo Physics (used by Mesh Flatten), Tetrino
(required by INP C3D4), Weaverbird, Millipede and Mecway.

## Where the workflow can shed cost before Dendro

1. **Trim before converting, and connect both Trim Lattice outputs.**
2. **Run Remove Short and Remove Floating on the Trimmed Lattice output only**, per the
   documentation's own advice. Trim stubs shorter than about two strut diameters add surface
   area and rasterisation work while adding nothing visible after the union.
3. **Avoid Remove by Valence** where the Untrimmed Lattice output already gives you the
   valence-1 removal for free — the documentation says so directly, and flags the component
   as very slow.
4. **Prefer a larger unit cell before a smaller voxel.** Lattice surface area is a linear
   term in the Dendro cost model; voxel size is quadratic. But cell size is a structural
   design decision.
5. **Consider arraying a rasterised Dendro volume rather than arraying curves**, where the
   lattice is uniform. Dendro volumes pass through the native Array and uniform Scale
   components. This does not apply to graded or conformal lattices, which is most of what
   Crystallon's Morph and Conformal tools exist to produce.
