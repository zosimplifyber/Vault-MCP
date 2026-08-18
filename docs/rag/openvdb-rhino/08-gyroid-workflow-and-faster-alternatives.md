# The Gyroid-on-Any-Geometry Workflow, and the Faster Routes to the Same Result

Source (reference video): "How to apply Gyroid lattice structure on any geometry in Grasshopper", https://youtu.be/o9br5xdym_o
Source: Chromodoris, https://github.com/camnewnham/ChromodorisGH
Source: Jellyfish, https://www.food4rhino.com/app/jellyfish and https://discourse.mcneel.com/t/jellyfish-1st-release/106041
Source: Cademy POC helmet gyroid tutorial, https://www.cademy.xyz/learn/poc-helmet-rhino-3d-modeling-gyroid-lattice-grasshopper-free-tutorial
Compiled for Simplifyber engineering, August 2026.

## What is verified about the reference workflow

The video title is confirmed: **"How to apply Gyroid lattice structure on any geometry in
Grasshopper."** The plug-in set in use is Dendro, Weaverbird and Crystallon.

The step-by-step of that specific video could not be extracted — YouTube does not serve the
transcript or description to a plain fetch. Everything below about the *shape* of the
workflow is inferred from the plug-in set and from the closely matching published tutorials,
and is marked as such. The performance analysis does not depend on the inference.

The canonical published form of this workflow, from the gyroid tutorial community, is:
generate the gyroid lattice with Crystallon, thicken it with Weaverbird Mesh Thicken, apply
Catmull-Clark subdivision with Weaverbird, and use Dendro to trim to the target geometry and
bake out a watertight mesh. Where the lattice is closed-cell rather than an open sheet,
Dendro does the thickening instead of Weaverbird.

**That published description is inaccurate on one point, and it matters.** Crystallon has no
gyroid or TPMS primitive — neither word appears anywhere in its 75 pages of official
documentation, and its Cell Type library is beam and shell unit cells populated into
TwistedBox container cells. Where a tutorial says "generate the gyroid with Crystallon", one
of two things is actually happening: the gyroid surface is coming from an isosurface tool
and Crystallon is only doing the voxel subdivision and trimming, or someone has hand-built a
unit cell that approximates a gyroid patch and is populating it with Cell Fill. The second
is a real technique but it is an approximation of a TPMS by tiled patches, not the minimal
surface itself, and it inherits none of the analytic advantages below.

## The structural problem with that workflow

A gyroid is a **surface defined by a closed-form implicit function**, not a network of
beams:

```
sin(x)·cos(y) + sin(y)·cos(z) + sin(z)·cos(x) = t
```

A sheet gyroid of finite thickness is just the region where the absolute value of that
expression is below a threshold. Everything about the geometry is available analytically at
any point in space, for the cost of three sines and three cosines.

The Crystallon-to-Dendro route throws that away. It converts an analytic field into explicit
Grasshopper geometry, then converts that explicit geometry back into a discretised field by
rasterisation, then converts the field back into explicit geometry. Two of those three
conversions exist only because the intermediate representation is Grasshopper geometry.

| Stage in the Crystallon → Weaverbird → Dendro gyroid workflow | What it costs | Is it structurally necessary for a gyroid? |
|---|---|---|
| Crystallon lattice generation stage | Interpreted native-component clusters producing tens of thousands of curves or a large mesh, single-threaded on the Grasshopper solver | No — the gyroid field is analytic |
| Weaverbird Mesh Thicken stage | Roughly doubles face count, adds a rim of side faces, cannot self-intersect-check | No — thickness is a threshold on the implicit function |
| Weaverbird Catmull-Clark stage | Multiplies face count by 4 per level, single-threaded, applied to the largest mesh in the definition | No — resolution is a grid parameter |
| Dendro rasterisation stage (Mesh to Volume or Curve to Volume) | Scales as lattice surface area over voxel size squared | No — the field could have been sampled directly |
| Dendro boolean trim against the part stage | One CSG pass, plus a hidden resample if voxel sizes differ | **Yes** — trimming to arbitrary geometry is genuinely a field operation |
| Dendro Volume to Mesh stage | Marching-cubes extraction, TBB-threaded | **Yes** — something has to extract the surface |

Four of the six stages are not structurally necessary.

## Why a TPMS cannot come from curves

Dendro's `Curve to Volume` wraps each curve in a spherical profile — a tube swept along a
1D skeleton. The union of tubes is a strut lattice. **There is no curve network whose
spherical sweep produces a minimal surface**, so the curve route, which is the right answer
for a beam lattice, cannot produce a TPMS at all.

This is not a resolution or tuning limitation. It is a difference in what the two things
are: a TPMS is a 2-manifold surface with near-zero mean curvature everywhere, and a swept
tube is a surface of revolution about a line.

The confusion arises because a gyroid *does* have a skeletal graph — the gyroid partitions
space into two interpenetrating labyrinths, each with its own skeletal net. Sweeping tubes
along that net gives a strut lattice that is topologically related to the gyroid and is
sometimes marketed as a "skeletal gyroid". It is a strut lattice. It has different surface
area, different mechanics, and is not a minimal surface.

### The two forms a TPMS is actually built in

Both are legitimately "TPMS structures" and neither is producible from curves.

| TPMS solid form | Definition from the implicit function `f(p)` | Character |
|---|---|---|
| Sheet (shell, double-walled) TPMS | `abs(f(p)) - t <= 0` — the surface itself given a finite thickness | Two parallel walls straddling the minimal surface; splits space into two separate labyrinths |
| Solid (network, skeletal) TPMS | `f(p) - t <= 0` — one labyrinth filled solid | A single connected solid network; the complement is the other labyrinth |

Both are one threshold on the same field, which is why the implicit route gives you either
for free while a geometry-based route needs a different construction for each.

### The common TPMS implicit functions

With `s = 2π / cell_size` and coordinates scaled as `x = s·P.x` and so on:

| TPMS | Implicit function `f(x, y, z)` |
|---|---|
| Gyroid | `sin x·cos y + sin y·cos z + sin z·cos x` |
| Schwarz P | `cos x + cos y + cos z` |
| Schwarz D (Diamond) | `sin x·sin y·sin z + sin x·cos y·cos z + cos x·sin y·cos z + cos x·cos y·sin z` |
| Neovius | `3(cos x + cos y + cos z) + 4·cos x·cos y·cos z` |
| IWP | `2(cos x·cos y + cos y·cos z + cos z·cos x) − (cos 2x + cos 2y + cos 2z)` |

Shifting the isovalue away from zero changes the volume fraction and breaks the minimality;
that is normally what is wanted for a printable part, since the true minimal surface has
zero thickness.

### Grading and conformality

The implicit route grades better than the cell-mapping route, because thickness and cell
size are just terms in the expression: make `t` a function of position, or of an attribute
sampled from a field, and the wall thickness varies smoothly with no cell seams.

One honest caveat: varying the **frequency** `s` spatially does not simply stretch the
lattice — it breaks periodicity and can produce discontinuities where neighbouring regions
fall out of phase. The robust approaches are to warp space before evaluating (deform the
sample coordinates and keep `s` constant) or to keep `s` constant and grade only the
thickness. Grading thickness is safe; grading frequency needs care.

## The direct implicit route

Sample a single scalar field on one grid and mesh it once.

1. Build the field as the combination of the gyroid function and the target part's signed
   distance. Intersection of two signed distance fields is the pointwise maximum; the
   thickened gyroid sheet is `|gyroid(p)| − c`. So the whole lattice-inside-a-part is one
   expression evaluated per grid point.
2. Extract the isosurface once, with marching cubes.

There is no rasterisation, no explicit intermediate geometry, no thicken, and no
subdivision. Field evaluation is embarrassingly parallel and both of the plug-ins below
exploit that.

| Plug-in for direct implicit sampling | What it provides | Notes |
|---|---|---|
| Chromodoris (camnewnham/ChromodorisGH) | Sample Voxels, Sample Voxels Custom, Build Isosurface, Close Voxel Data, QuickSmooth | "The majority of code is optimized and multithreaded", with the voxel sampler using "a multi-threaded KD-Tree" that "switches compute modes based on historical performance". Isosurfacing builds on toxiclibs. GPL-3.0. Voxel data passes as `Single[x,y,z]` arrays. |
| Jellyfish (Siming Mei, first released July 2020) | Implicit modelling components over signed distance fields | Built on Ryan Schmidt's geometry3Sharp, using "marching cubes algorithm with Parallel Computing option". Triangle meshes only; converts to and from Rhino meshes automatically. |
| Millipede IsoSurface | Isosurface extraction from a sampled field | The long-standing route for TPMS in Grasshopper; single component, well documented in the TPMS literature. |

**Sample Voxels Custom in Chromodoris is the component that matters here**, because it
accepts an arbitrary per-point expression — which is what lets the gyroid function and the
part's distance field be combined in one pass.

The "Close Voxel Data" component exists precisely for the part-boundary problem: it caps the
field at the domain edge so the extracted mesh comes out closed rather than open where the
lattice meets the boundary of the sampling box.

## When Dendro is still the right tool

The direct implicit route is not a general replacement. Dendro remains the correct choice
for:

- **Trimming or booleaning against arbitrary Brep, SubD or scanned mesh geometry**, where no
  analytic distance function exists. Mesh to Volume is the bridge.
- **Offsets and shelling**, where `Offset Volume` gives a topologically robust result that
  mesh offsetting cannot.
- **Mask-limited filtering**, where only part of the model should be smoothed.
- **Beam and strut lattices** that are genuinely a network of curves rather than a TPMS —
  here Curve to Volume's immunity to junction self-intersection is the whole point.
- **`.vdb` interchange** with Houdini, Maya or a downstream slicer.

The efficient hybrid for a gyroid-in-a-part is therefore: **sample and mesh the gyroid
implicitly; use Dendro only for the part it cannot express analytically.** In many cases
that reduces Dendro's role to a single Mesh to Volume of the part plus one Volume
Intersection.

## Rhino 8's own OpenVDB path

Rhino 8 bundles OpenVDB 10.0.0 and `ShrinkWrap` is built on it, using signed distance
functions internally. For the specific job of turning a messy or open mesh into a watertight
one, ShrinkWrap is a native, threaded, no-plugin-conflict alternative to the
Mesh to Volume → Volume to Mesh round trip.

| ShrinkWrap option | Meaning | Effect on speed and result |
|---|---|---|
| Target Edge Length in ShrinkWrap | Approximate output mesh edge length; auto-calculated by default | The primary resolution control. Deviates more as Polygon Optimization rises. |
| Offset in ShrinkWrap | Adjusts the output mesh by a distance in model units | Positive inflates and adds faces; negative shrinks and reduces face count. Less influence on edge length than the other parameters. |
| Smoothing Iterations in ShrinkWrap | Smoothing strength; default 0, no upper limit | Higher values reduce face count and deflate the mesh further. |
| Polygon Optimization % in ShrinkWrap (0–100) | Adaptively removes vertices | The equivalent of Dendro's Adaptivity — higher values cut faces while keeping edges along hard features. |
| Inflate Vertices and Points in ShrinkWrap | Builds the output from each input vertex or point | On by default for points and point clouds, off for other types. Inflated meshes merge where they intersect. Uses render mesh vertices for SubD and NURBS. |
| Fill Holes in Input Objects in ShrinkWrap | Fills holes before meshing; on by default | "very helpful in most cases" but will close micro-gaps by design. |
| Compute Vertex Colors in ShrinkWrap | Transfers vertex and display colours to the output | — |

ShrinkWrap is also exposed as a Grasshopper component in Rhino 8, so it can sit inside the
definition rather than being a manual post-step.

## Summary of the recommendation for a gyroid-in-a-part workflow

1. Replace Crystallon-plus-Weaverbird gyroid generation with a **direct implicit sample and
   isosurface** (Chromodoris Sample Voxels Custom, or Jellyfish, or Millipede IsoSurface).
   Thickness becomes a threshold, not a mesh operation.
2. Keep Dendro for the part boundary — one Mesh to Volume of the target geometry and one
   Volume Intersection — and only if the part cannot be expressed as a distance function.
3. Drop Weaverbird Catmull-Clark entirely. Resolution comes from the sampling grid.
4. If Weaverbird smoothing is still wanted for finish, use **LaplacianHC**, not plain
   Laplacian, so wall thickness is preserved.
5. Consider ShrinkWrap for the final watertighting step instead of a Dendro round trip; it
   is native, threaded, and avoids the plug-in OpenVDB conflict described in
   `10-rhino8-openvdb-and-environment.md`.
