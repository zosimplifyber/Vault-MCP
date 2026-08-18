# Weaverbird — Mesh Operations, and Where They Belong in the Order

Source: Weaverbird, Giulio Piacentino, https://www.giuliopiacentino.com/weaverbird/
Source: Grasshopper Docs component pages for Mesh Thicken, Catmull-Clark Subdivision, Laplacian Smoothing and LaplacianHC Smoothing, https://grasshopperdocs.com/components/weaverbird/
Source: NExT Lab, "Thicken a Mesh with Grasshopper", https://ms-kb.msd.unimelb.edu.au/next-lab/3d-printing/key-techniques/mesh-techniques/other-techniques/thicken-a-mesh-using-weaverbird-and-grasshopper
Source: Cademy, POC helmet gyroid lattice tutorial, https://www.cademy.xyz/learn/poc-helmet-rhino-3d-modeling-gyroid-lattice-grasshopper-free-tutorial
Compiled for Simplifyber engineering, August 2026.

Weaverbird is a topological mesh editor for Grasshopper by Giulio Piacentino. In a gyroid
or lattice workflow it does two jobs: giving an open surface thickness, and smoothing.
Both have a strongly preferred position in the pipeline.

## The components that appear in this workflow

| Weaverbird component | What it does | Face-count behaviour |
|---|---|---|
| Mesh Thicken (wbThicken) in Weaverbird | Offsets a mesh to give it thickness, by offsetting individual vertices and constructing new side faces | Roughly doubles face count, plus a rim of side faces around every boundary edge |
| Catmull-Clark Subdivision (wbCatmullClark) in Weaverbird | Recursive subdivision after Catmull and Clark, 1978; the result "always consist[s] of quadrilaterals" | **Multiplies face count by about four per level.** Two levels is 16×, three is 64×. |
| Laplacian Smoothing (wbLaplacianSmoothing) in Weaverbird | Moves each vertex toward the average of its neighbours | Face count unchanged; volume shrinks with iterations |
| LaplacianHC Smoothing (wbLaplacianHC) in Weaverbird | Laplacian smoothing with the Vollmer-Mencl-Müller HC correction that pushes vertices back toward their originals | Face count unchanged; much less shrinkage than plain Laplacian |

## The thicken-versus-Dendro decision

The documented rule of thumb from the gyroid tutorial community is precise and worth
keeping: **Weaverbird's Mesh Thicken works well on open meshes like a gyroid surface; for
closed-cell meshes, use Dendro to thicken instead.**

The reason is that wbThicken is a vertex-offset operation with no self-intersection
handling. On a smooth open surface such as a single gyroid sheet, offsetting vertices along
their normals is well behaved. On a closed-cell or beam lattice, offsetting produces
self-intersections wherever the offset distance approaches the local radius of curvature or
where struts meet, and the result is a mesh that renders but will not bake, boolean or
print.

Dendro's equivalent — Offset Volume, or simply rasterising with a radius — cannot
self-intersect, because the union is implicit in the distance field.

| Geometry being thickened | Preferred tool | Why |
|---|---|---|
| Open gyroid or TPMS sheet, moderate thickness relative to curvature | Weaverbird Mesh Thicken | Fast, exact, no voxel discretisation, no resolution decision |
| Closed-cell lattice, beam lattice, or any junction-heavy network | Dendro (Curve to Volume, or Mesh to Volume plus Offset Volume) | Immune to self-intersection at junctions |
| Thickness approaching the local radius of curvature | Dendro | Vertex offset will invert |

## Subdivision after Dendro is almost always the wrong order

This is the most expensive avoidable mistake in the combined workflow.

Catmull-Clark quadruples face count per level. A Volume to Mesh output for a gyroid infill
at a 0.25 mm voxel size is on the order of three million faces (see
`03-voxel-cost-model.md`). One level of Catmull-Clark takes it to twelve million; two levels
to forty-eight million. Weaverbird runs on the Grasshopper solver thread, and its cost is
linear in face count, so this is minutes of single-threaded work producing a mesh Rhino will
struggle to display, let alone bake.

There are three better places to get the same smoothness:

1. **Smooth in the volume domain.** Dendro's Smooth Volume with Type 1 (Laplacian) and a
   small iteration count operates on the level set and does not increase the output face
   count at all. On a true signed distance field, OpenVDB documents Laplacian diffusion as
   identical to mean-curvature diffusion and cheaper.
2. **Subdivide the unit cell, not the assembly.** If the smoothness is wanted in the cell
   geometry, apply Catmull-Clark to the single cell before arraying. The cost is divided by
   the number of cells.
3. **Reduce voxel size instead.** Doubling resolution costs 4× in the volume domain; one
   Catmull-Clark level costs 4× in the mesh domain but on a mesh that is already the largest
   object in the definition, and it adds no actual surface information — it only interpolates
   what the voxel grid already decided.

The general principle: **do smoothing while the data is still a volume, and do subdivision
while the data is still small.**

## Laplacian versus LaplacianHC

Plain Laplacian smoothing shrinks the mesh, and the shrinkage compounds with iterations. On
a lattice with thin struts this is not cosmetic — enough iterations will thin the struts
below the intended radius or pinch them off entirely.

LaplacianHC applies the Vollmer-Mencl-Müller correction, pulling vertices back toward their
original positions each pass, and preserves volume far better. On any geometry where wall
thickness is a specification rather than an aesthetic, LaplacianHC is the correct default.

A commonly cited starting point for the Refine tool's coupled settings is to give the same
value, "perhaps between 5 to 15", to Samples, Laplacian smoothing and Valence passes.

## The role of Weaverbird in the reference workflow

In the Crystallon-plus-Weaverbird gyroid tutorials the documented sequence is: generate the
gyroid lattice with Crystallon, thicken with Mesh Thicken, then apply Catmull-Clark
subdivision with Weaverbird — with Dendro brought in to bake out the final mesh, or to do
the thickening where the mesh is closed-cell rather than an open sheet.

That sequence is correct for a modest demonstration model. It does not scale, for exactly
the reason above: the subdivision multiplier lands on the largest mesh in the definition. On
a production-size part, move the smoothing into the volume domain.
