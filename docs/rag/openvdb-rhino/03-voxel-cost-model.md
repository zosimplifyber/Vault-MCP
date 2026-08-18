# The Voxel Cost Model — Why a Dendro Definition Is Slow, in Arithmetic

Derived for Simplifyber engineering, August 2026, from the documented behaviour in
`01-openvdb-data-structure.md` and `02-openvdb-tools-and-their-cost.md`.

The formulas below are derived, not quoted from a vendor. The mechanisms they rest on are
documented (narrow band width is specified in voxel units; the offset CFL is
`0.5 × voxelSize`; Laplacian diffusion time step is `dx²/6`), but the constant factors for
memory per voxel are estimates. Treat the scaling exponents as reliable and the absolute
millisecond and megabyte figures as order-of-magnitude.

## The one equation

For a narrow-band level set:

```
active voxels  N  ≈  (A / s²) × 2·bw
```

where `A` is the **boundary surface area** in world units, `s` is the voxel size in world
units, and `bw` is the bandwidth in voxel units.

Three things follow, and they are the whole tuning story:

| Change to a Dendro definition | Effect on active voxel count | Why |
|---|---|---|
| Halving voxel size in a Dendro definition | Multiplies active voxels by 4 | Area term `A/s²` is quadratic. It is **not** cubic, because bandwidth is counted in voxels, so the band gets physically thinner as `s` shrinks. |
| Doubling bandwidth in a Dendro definition | Multiplies active voxels by 2 | Linear term. The cheapest lever in the whole stack, and the most often left at a value larger than needed. |
| Hollowing out or shelling the part in a Dendro definition | Roughly doubles active voxels | Cost follows boundary area, not enclosed volume. A shell has two boundaries. A solid block and a hollow box of the same outer size cost about the same; a shelled box costs twice that. |

**Enclosed volume does not appear in the equation.** This is the single most common wrong
mental model. People assume a bigger part costs more; what actually costs more is a part
with more surface. That is exactly why lattices are the pathological case.

## Why lattices are the pathological case

A lattice has an enormous boundary area inside a small bounding box.

### Worked example — gyroid infill

A part of 100 × 100 × 30 mm, gyroid unit cell 10 mm, walls thickened to 1 mm.

- Part volume 300,000 mm³, so 300 unit cells.
- The gyroid minimal surface has an area of about 3.09 L² per cubic cell of edge L, so
  about 309 mm² per 10 mm cell, about 92,700 mm² of mid-surface.
- Thickening to a solid wall gives two boundaries, so `A ≈ 185,000 mm²`.

At bandwidth 3 (a band 6 voxels thick in total):

| Voxel size for the gyroid example | Active voxels | Rough level-set memory | Rough face count out of Volume to Mesh at adaptivity 0 |
|---|---|---|---|
| 0.50 mm voxel size for the gyroid example | 4.4 million | ~40 MB | ~740,000 |
| 0.25 mm voxel size for the gyroid example | 17.8 million | ~160 MB | ~3.0 million |
| 0.125 mm voxel size for the gyroid example | 71 million | ~640 MB | ~11.9 million |

The memory estimate assumes roughly 4.3 bytes per voxel for a fully occupied 8³ leaf
(512 floats plus the value and state masks) inflated by a factor of about two, because
leaves straddling a surface are only partly occupied.

### Worked example — BCC strut lattice

Same part, 5 mm body-centred-cubic cell, 0.5 mm strut radius.

- 20 × 20 × 6 = 2,400 cells, 8 centre-to-corner struts each, each 4.33 mm long.
- 19,200 struts, about 83,000 mm of total strut length.
- Lateral area `A = 2πrL ≈ 261,000 mm²`.
- The strut radius sets a **floor** on voxel size: roughly three voxels across the radius
  are needed before a strut stops looking faceted, so `s ≤ 0.167 mm`.
- At `s = 0.167 mm` and bandwidth 3: about **56 million active voxels**.

That floor is the trap. The strut radius, not the part size and not the cell size, is what
forces the voxel size down, and the voxel size is what quadratically drives everything else.

## The derived scaling laws

| Operation in a Dendro / OpenVDB pipeline | How its cost scales with voxel size s | Practical reading |
|---|---|---|
| Mesh to Volume, Curve to Volume, Point Cloud to Volume | ∝ 1/s² | Halving voxel size is a 4× penalty. |
| Boolean (Union, Intersect, Difference) | ∝ 1/s² | Same 4× penalty, plus a hidden extra full resample if the two inputs were built at different voxel sizes. |
| Volume to Mesh | ∝ 1/s² for the extraction; output face count also ∝ 1/s² | Adaptivity reduces the face count, not the extraction work. |
| Offset by a fixed world distance D | ∝ 1/s³ | Voxel count grows as 1/s² *and* the CFL-limited iteration count grows as 2D/s. This is the steepest common operation. |
| Smooth with a fixed iteration count | ∝ 1/s² | What most people actually do. |
| Smooth to a fixed physical radius R | ∝ 1/s⁴ | Voxels grow as 1/s² and, since the Laplacian time step is s²/6, the iterations needed to diffuse a fixed distance grow as (R/s)². Rarely intended, easily hit by "the smoothing looks weaker at high res so I turned iterations up." |

The last row explains a common and demoralising experience: someone halves the voxel size
for a finer result, notices the smoothing now looks too subtle, quadruples the iterations to
compensate, and the definition becomes sixteen times slower than before.

## The downstream mesh is usually the real bottleneck

A 3-million-face mesh coming out of Volume to Mesh is not the end of the cost. In
Grasshopper it is then held in memory with normals and topology, previewed by the display
pipeline every frame, passed to any downstream component, and baked. A rough budget of
60 to 80 bytes per face with vertices, normals and face topology puts a 3-million-face
lattice at 200 MB or more of mesh, on top of the level set that produced it.

Two consequences:

1. **Disable preview on every Dendro output while working.** The display pipeline cost is
   paid on every viewport redraw, not once per solve.
2. **Adaptivity is nearly free money.** It does not speed up the volume work, but a value
   of 0.1 to 0.3 typically removes a large fraction of the faces in the flat regions of a
   lattice with no visible change, and every downstream stage gets proportionally cheaper.

## The order in which to spend the budget

Given `N ≈ (A/s²) × 2·bw`, the levers in order of return per unit of effort:

1. **Reduce A.** Larger unit cell, or fewer lattice cells, or trim the lattice to the
   region that actually needs it. This is linear and usually the largest single win, and
   it is a design decision rather than a settings decision.
2. **Raise the strut radius or wall thickness.** This raises the voxel-size floor, and the
   voxel size term is quadratic. Going from 0.4 mm to 0.6 mm strut radius permits a 50%
   larger voxel and therefore cuts the voxel count by more than half.
3. **Lower bandwidth to the minimum the operations need.** Linear, free, and usually left
   too high. Two is the practical minimum for a reliable zero crossing.
4. **Raise voxel size while working, lower it only for the final bake.** Quadratic.
5. **Raise adaptivity.** Costs nothing in the volume domain, pays off everywhere downstream.
