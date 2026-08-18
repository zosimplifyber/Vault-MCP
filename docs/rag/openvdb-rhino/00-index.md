# OpenVDB in Rhino — Collection Index

Scope: OpenVDB as it is actually reached from Rhino and Grasshopper — through the Dendro
plug-in, alongside Crystallon for lattice generation and Weaverbird for mesh operations, and
through Rhino 8's own bundled OpenVDB behind ShrinkWrap. Compiled for Simplifyber
engineering, August 2026, in response to a gyroid-lattice workflow that runs slowly.

These are structured technical summaries written from primary documentation, plug-in source
code and vendor forums, with the source URL on every document. They are not scraped article
text. Derived arithmetic is labelled as derived.

## The central finding

**The cost of an OpenVDB operation is set by boundary surface area, not by enclosed volume.**

```
active voxels  N  ≈  (A / s²) × 2·bw
```

`A` is boundary surface area in world units, `s` is voxel size in world units, `bw` is
bandwidth **in voxel units**. Every tuning decision follows from this.

| Lever on a Dendro definition | Multiplier on cost | Why |
|---|---|---|
| Halving voxel size in a Dendro definition | ×4 | The area term is quadratic. It is not cubic, because bandwidth is counted in voxels, so the band gets physically thinner as resolution rises. |
| Doubling bandwidth in a Dendro definition | ×2 | Linear, and the most commonly overlooked setting. |
| Offsetting by a fixed world distance in a Dendro definition | ∝ 1/s³ | Voxel count grows as 1/s² and the CFL-limited iteration count grows as 2·distance/s on top. |
| Smoothing to a fixed physical radius in a Dendro definition | ∝ 1/s⁴ | The Laplacian time step is s²/6, so the iterations needed to diffuse a fixed distance grow as (R/s)² over four times the voxels. |
| Making the part smaller in a Dendro definition | no direct effect | Enclosed volume does not appear in the equation at all. |

That last row is the misconception this collection exists to correct. A lattice is slow not
because it is large but because it packs an enormous amount of surface into a small box: a
100 × 100 × 30 mm part with a 10 mm gyroid cell has roughly 185,000 mm² of boundary, which
at a 0.25 mm voxel is about 18 million active voxels and a 3-million-face output mesh.

## The second finding, specific to gyroids

A gyroid is a surface with a closed-form implicit function, not a network of beams. The
conventional Crystallon → Weaverbird → Dendro pipeline has six stages, and **four of them
are not structurally necessary**: lattice generation, mesh thickening, subdivision and
rasterisation all exist only because the intermediate representation is Grasshopper
geometry. Sampling the implicit function directly and running marching cubes once —
Chromodoris Sample Voxels Custom, Jellyfish, or Millipede IsoSurface — collapses those four
into one threaded pass. Dendro's genuinely necessary role reduces to trimming against the
target part, which is a field operation with no analytic form.

## The third finding — two words that mean different things

**Crystallon's "voxel" is not OpenVDB's voxel, and Crystallon has no gyroid.**

Crystallon ships a tool group called Voxelize, and every one of its components outputs the
Grasshopper type `twistedbox` — a deformable eight-corner container cell "to be filled with
unit cells". It carries no sample value: no distance, no density, no occupancy. It is a
cage, not a sample. Crystallon therefore **cannot export a voxel representation in the
OpenVDB sense**; its only export formats are LTCX (a beam graph of nodes, connections and
radii) and Abaqus INP (an FEA mesh). The route to a `.vdb` runs through Dendro.

Separately, the words "gyroid" and "TPMS" appear **zero times** in the 75 pages of official
Crystallon documentation. Its Cell Type library is beam and shell unit cells. Tutorials
describing "a gyroid generated with Crystallon" are conflating it with an isosurface step
performed elsewhere.

Both points are covered in `06-crystallon-lattice-generation.md`.

## Documents in this collection

| Document file in the OpenVDB in Rhino collection | Primary source | What that document covers |
|---|---|---|
| `00-index.md` | — | Collection index, the cost equation, and the two central findings |
| `01-openvdb-data-structure.md` | OpenVDB Overview and FAQ | The VDB tree and its branching factors, tiles and background values, active vs inactive voxels, level set vs fog volume, ValueAccessor caching and its thread rules, the per-thread-grid merge pattern |
| `02-openvdb-tools-and-their-cost.md` | OpenVDB VolumeToMesh.h and LevelSetFilter.h | meshToVolume, createLevelSet, ParticlesToLevelSet, the CSG functions, every LevelSetFilter operation with its documented time step and relative cost, volumeToMesh isovalue and adaptivity |
| `03-voxel-cost-model.md` | Derived | The cost equation, the derived scaling laws per operation, worked gyroid and BCC-strut examples with voxel counts and memory, why the output mesh is usually the real bottleneck |
| `04-dendro-component-reference.md` | ECR Labs Dendro documentation v0.01.00 | All 14 components, the four Settings inputs with vendor guidance, converters, the invalid-mesh problem, filters and masks, booleans, the native-component compatibility chart, installation requirements |
| `05-dendro-to-openvdb-call-mapping.md` | DendroAPI/DendroGrid.cpp | Which OpenVDB call sits behind each Dendro component; bandwidth is in voxel units and applied symmetrically; mismatched Settings force a GridTransformer resample per boolean; isovalue is normalised by voxel size |
| `06-crystallon-lattice-generation.md` | Official Crystallon documentation (75 pp) and McNeel forum | Full component reference by group; why Crystallon's TwistedBox "voxel" is not an OpenVDB voxel and cannot be exported as one; that Crystallon has no gyroid or TPMS primitive; the LTCX and INP export formats; the Trim Lattice two-output trap that silently hollows a lattice; the curves-plus-radii handoff to Dendro |
| `07-weaverbird-mesh-operations.md` | Weaverbird and Grasshopper Docs | Mesh Thicken vs Dendro for open sheets against closed cells; why Catmull-Clark after Dendro is the wrong order; Laplacian vs LaplacianHC and wall-thickness preservation |
| `08-gyroid-workflow-and-faster-alternatives.md` | Reference video, Chromodoris, Jellyfish, Rhino help | The reference workflow and what is verified about it; the stage-by-stage necessity audit; the direct implicit route; when Dendro is still correct; the full ShrinkWrap option table |
| `09-performance-playbook.md` | Synthesis | Ordered actions from free display fixes through settings, pipeline reordering, design changes and approach changes; invalid-mesh diagnosis; what will not help |
| `10-rhino8-openvdb-and-environment.md` | McNeel forum and Rhino help | Rhino 8 bundles OpenVDB 10.0.0 and the resulting plug-in DLL conflict; ShrinkWrap as the native path; Dendro repositories and versions; the two installation gotchas; single-thread vs multi-thread hardware split |
| `11-source-index.md` | — | Every source consulted, what it was used for, and its caveats |
| `12-porting-the-workflow-to-houdini.md` | SideFX node reference and the Crystallon documentation | Dendro-to-Houdini SOP mapping (both wrap the same OpenVDB); Crystallon's Voxelize, Populate, Modify and Thicken logic mapped onto Lattice from Volume, Copy to Points, Point Deform and `pscale`; the gyroid as three nodes and a Volume Wrangle; LTCX and INP as the only real gaps; the `.vdb` bridge that needs no porting |

## What is verified and what is inferred

Verified from primary sources: everything about the OpenVDB tree, filters, time steps and
CFL limits; every Dendro component, input and vendor recommendation; the exact OpenVDB call
behind each Dendro component; the Trim Lattice two-output behaviour; Rhino 8's OpenVDB
10.0.0 and the plug-in conflict; every ShrinkWrap option.

Derived, with the mechanism sourced but the constants estimated: the cost equation and all
scaling exponents in `03-voxel-cost-model.md`, the worked voxel counts, and the memory and
face-count budgets.

Inferred and labelled as such: the step-by-step content of the reference video. Its title is
confirmed but YouTube did not serve the transcript or description. The workflow shape
attributed to it comes from the plug-in set and from matching published tutorials, and none
of the performance analysis depends on it.

## Open questions that need a benchmark, not more searching

1. **Where does the time actually go in the current definition?** No source gives concrete
   timings for a Crystallon-plus-Dendro lattice, and the two published forum threads on
   the subject contain no numbers at all. The Crystallon half and the Dendro half need
   timing separately before any tuning; they have opposite remedies and opposite hardware
   preferences.
2. **Is Dendro in this installation actually resolving its own OpenVDB?** Rhino 8 ships
   OpenVDB 10.0.0 and a documented incompatibility exists with third-party wrappers. Worth
   confirming which DLL the process loads before attributing any misbehaviour to the
   definition.
3. **How much does the direct implicit route actually win here?** The argument that four of
   six stages are unnecessary is structural, not measured. A side-by-side on one real part —
   Crystallon-plus-Dendro against Chromodoris Sample Voxels Custom — would settle it in an
   afternoon and is the highest-value experiment available.
4. **What is the minimum viable bandwidth for this workflow?** Two voxels is the theoretical
   floor for a reliable zero crossing, but the offset and smoothing steps move the interface
   and may need more. This is a one-parameter sweep with a linear payoff.
5. **Which unit cell is the current definition actually using, and could a coarser one
   serve?** The official Crystallon reference is now captured in
   `06-crystallon-lattice-generation.md`, but the Cell Type library is documented only as a
   set of named categories with a data-tree diagram; the individual cell topologies and
   their relative strut counts are not enumerated in the documentation and would have to be
   read off the component in Grasshopper. Strut count per cell is a linear term in the cost
   model.
