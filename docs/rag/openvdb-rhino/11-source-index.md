# Source Index — OpenVDB in Rhino Collection

Every source consulted in compiling this collection, August 2026, with what it was used for
and any caveat on its reliability.

## OpenVDB primary documentation

| OpenVDB source | URL | Used for |
|---|---|---|
| OpenVDB documentation landing page | https://www.openvdb.org/documentation/ | Entry point for the doxygen reference |
| OpenVDB Overview | https://www.openvdb.org/documentation/doxygen/overview.html | Tree structure, branching factors, active/inactive voxels, tiles, level set vs fog volume, ValueAccessor, iterators |
| OpenVDB Frequently Asked Questions | https://www.openvdb.org/documentation/doxygen/faq.html | Memory and access performance claims, ValueAccessor thread safety, the per-thread-grid-then-merge multithreading pattern |
| OpenVDB VolumeToMesh.h source | https://www.openvdb.org/documentation/doxygen/VolumeToMesh_8h_source.html | Isovalue and adaptivity semantics, quad vs triangle output, TBB threading, reference-meshing overhead figure |
| OpenVDB LevelSetFilter.h source | https://www.openvdb.org/documentation/doxygen/LevelSetFilter_8h_source.html | Filter time steps (dx²/3, dx²/6), Gaussian as 4 mean passes, median cost, offset CFL limit, alpha mask semantics |
| OpenVDB LevelSetUtil.h source | https://www.openvdb.org/documentation/doxygen/LevelSetUtil_8h_source.html | Narrow-band half-width guidance, interior voxel deactivation, PDE renormalisation |

## Dendro

| Dendro source | URL | Used for | Caveat |
|---|---|---|---|
| DENDRO Voxel plug-in for Grasshopper, Documentation v0.01.00, ECR Labs (14 pp) | https://cdck-file-uploads-global.s3.dualstack.us-west-2.amazonaws.com/mcneel/uploads/default/original/4X/f/d/a/fda9159b497568e1aacedaa03a13cf74e6efe433.pdf | Every component, the four Settings inputs and their vendor guidance, filters, masks, booleans, IO, compatibility chart, installation | Version 0.01.00, and the vendor calls Dendro "a beta product being actively updated" |
| Dendro repository, ryein | https://github.com/ryein/dendro | Build dependencies, licence, Rhino 8 targeting | — |
| Dendro repository, ecrlabs | https://github.com/ecrlabs/dendro | The repository the official documentation points to | Two repositories exist for the same project |
| DendroAPI/DendroGrid.cpp | https://github.com/ryein/dendro/blob/master/DendroAPI/DendroGrid.cpp | The exact OpenVDB call behind each component, the GridTransformer/BoxSampler resample in the boolean path, isovalue normalisation by voxel size | Read via the raw file; line numbers not recorded |
| Dendro on Food4Rhino | https://www.food4rhino.com/app/dendro | Distribution and vendor forum | — |
| Grasshopper Docs, Dendro addon | https://grasshopperdocs.com/addons/dendro.html | Canonical component names, nicknames and categories | Input/output parameter detail is absent from this listing |
| Grasshopper Docs, Create Settings | https://grasshopperdocs.com/components/dendro/createSettings.html | Settings component confirmation | — |
| Grasshopper Docs, Curve To Volume | https://grasshopperdocs.com/components/dendro/curveToVolume.html | Curve conversion confirmation | — |
| Parametric House, Dendro | https://parametrichouse.com/dendro/ | Secondary tutorial context | Tutorial site, not authoritative |

## Crystallon

| Crystallon source | URL | Used for | Caveat |
|---|---|---|---|
| Crystallon repository | https://github.com/GHCrystallon/Crystallon | Project description, cluster architecture, adjacent-plug-in list, applications, GPL-3.0 licence | — |
| About Crystallon | https://www.grasshopper3d.com/group/crystallon/page/about-crystallon | Modular editable-cluster design rationale | — |
| FATHOM, Introducing Crystallon | https://fathommfg.com/blog/introducing-crystallon/ | Origin at FATHOM, author Aaron Porterfield | — |
| f=f blog, Crystallon page | http://fequalsf.blogspot.com/p/crystallon.html | Applications and adjacent-plug-in list | Page did not yield a component-level listing |
| CRYSTALLON Lattice structure tools for Grasshopper3D (PDF, 75 pp) | https://cdck-file-uploads-global.s3.dualstack.us-west-2.amazonaws.com/mcneel/uploads/mcneel_it/original/3X/3/c/3c7fdf996e3f19318e266ce06956e45290e464d1.pdf | The full component reference — every component's description, inputs and output types; the voxel glossary definition and the lattice anatomy diagram; the Dependencies page; the Trim Lattice output definitions; the LTCX and INP writers | 19 MB, so it exceeds the WebFetch limit; retrieved with curl and extracted with pypdf. Confirmed by full-text search to contain zero occurrences of "gyroid" or "TPMS" |
| Crystallon repository file tree (GitHub API) | https://api.github.com/repos/GHCrystallon/Crystallon/contents/ | The shipped `.ghuser` component list by folder — 2D, Beta, Modify, Populate, Thicken, Utilities, Voxelize | Includes Beta components (Boundary Voxels, Conformal Skin, Connectivity Selector, Connectivity Type) that are absent from the documentation |

## Weaverbird

| Weaverbird source | URL | Used for |
|---|---|---|
| Weaverbird, Giulio Piacentino | https://www.giuliopiacentino.com/weaverbird/ | Authorship and scope as a topological mesh editor |
| Grasshopper Docs, Mesh Thicken | https://grasshopperdocs.com/components/weaverbird/meshThicken.html | wbThicken vertex-offset mechanism |
| Grasshopper Docs, Catmull-Clark Subdivision | https://grasshopperdocs.com/components/weaverbird/catmullClarkSubdivision.html | Quadrilateral-only output, 1978 origin |
| Grasshopper Docs, Laplacian Smoothing | https://grasshopperdocs.com/components/weaverbird/laplacianSmoothing.html | Plain Laplacian behaviour |
| Grasshopper Docs, LaplacianHC Smoothing | https://grasshopperdocs.com/components/weaverbird/laplacianHCSmoothing.html | HC volume-preserving variant |
| NExT Lab, Thicken a Mesh with Grasshopper | https://ms-kb.msd.unimelb.edu.au/next-lab/3d-printing/key-techniques/mesh-techniques/other-techniques/thicken-a-mesh-using-weaverbird-and-grasshopper | wbThicken practical guidance |
| Parametric3D, Weaverbird tutorial | https://parametric3d.com/en/weaverbird-grasshopper/ | Refine tool settings guidance |

## Implicit and isosurface alternatives

| Alternative-route source | URL | Used for |
|---|---|---|
| ChromodorisGH repository | https://github.com/camnewnham/ChromodorisGH | Component list (Sample Voxels, Sample Voxels Custom, Build Isosurface, Close Voxel Data, QuickSmooth), multithreaded KD-Tree sampler, toxiclibs basis, GPL-3.0 |
| Chromodoris group | https://www.grasshopper3d.com/group/chromodoris | Positioning as a fast voxel sampler and isosurfacer |
| Jellyfish on Food4Rhino | https://www.food4rhino.com/app/jellyfish | Implicit modelling over SDFs on geometry3Sharp |
| Jellyfish 1st release thread | https://discourse.mcneel.com/t/jellyfish-1st-release/106041 | Author Siming Mei, July 2020, geometry3Sharp marching cubes with parallel computing, triangle-mesh-only constraint |
| Parametric House, Jellyfish | https://parametrichouse.com/jellyfish/ | Secondary description |
| Scaffolder | https://github.com/nodtem66/Scaffolder | Standalone TPMS/gyroid scaffold generation from STL by implicit function — an out-of-Rhino comparison point |

## Rhino 8 platform

| Rhino platform source | URL | Used for |
|---|---|---|
| ShrinkWrap command help | http://docs.mcneel.com/rhino/8/help/en-us/commands/shrinkwrap.htm | Every ShrinkWrap option and its effect |
| Rhino ShrinkWrap feature page | https://www.rhino3d.com/en/features/shrinkwrap/ | Supported input types and use cases |
| Rhino 8 Feature: ShrinkWrap (forum) | https://discourse.mcneel.com/t/rhino-8-feature-shrinkwrap/149658 | ShrinkWrap built on signed distance functions |
| Rhino 8 ShrinkWrap component (forum) | https://discourse.mcneel.com/t/rhino-8-shrinkwrap-component/169527 | Grasshopper exposure of ShrinkWrap |
| Are Open VDBs Rhino's new geometry type? | https://discourse.mcneel.com/t/are-open-vdbs-rhinos-new-geometry-type-vdb-to-native-machine-tool-paths/189999 | OpenVDB.dll shipped in Rhino 8 |

## McNeel forum threads

| Forum thread | URL | What it established |
|---|---|---|
| OpenVDB incompatibility | https://discourse.mcneel.com/t/openvdb-incompatibility/193906 | Rhino 8 bundles OpenVDB 10.0.0 (Nathan Letwory); third-party wrappers that worked in R7 break in R8 (Mariusz Hermansdorfer); workaround is to build OpenVDB under a different output name (Dale Fugier) |
| Using Dendro with Grasshopper C# | https://discourse.mcneel.com/t/using-dendro-with-grasshopper-c-how-do-developers-handle-openvdb/214146 | DendroAPI/DendroGH architecture; calling DendroGH.dll directly is faster than the canvas components (Laurent Delrieu); no Rhino-internal OpenVDB shortcut |
| Problem with creating a lattice infill using crystallon with dendro | https://discourse.mcneel.com/t/problem-with-creating-a-lattice-infill-using-crystallon-with-dendro/156184 | Trim Lattice has two outputs and both must feed Curve to Volume (Aaron Porterfield); connecting one produces a hollow lattice |
| Increasing Dendro resolution | https://discourse.mcneel.com/t/increasing-dendro-resolution/156138 | Staged low-res-then-high-res workflow for a configurator; large resolution jumps fail; Pufferfish Rebuild Mesh with Merge Vertices as an invalid-mesh fix |
| Grasshopper performance with Intralattice/Dendro issue | https://discourse.mcneel.com/t/grasshopper-performance-with-intralattice-dendro-issue/74468 | Lock the solver and use a Data Dam for heavy definitions (Michael Pryor); no concrete benchmark numbers in the thread |

## Houdini

| Houdini source | URL | Used for |
|---|---|---|
| VDB from Particles SOP | https://www.sidefx.com/docs/houdini/nodes/sop/vdbfromparticles.html | `pscale` as the per-point radius attribute; the 1.5-voxel minimum radius; half-band voxels; SDF versus fog output |
| VDB from Polygons SOP | https://www.sidefx.com/docs/houdini/nodes/sop/vdbfrompolygons.html | The `meshToVolume` equivalent; voxel size semantics |
| Lattice from Volume SOP | https://www.sidefx.com/docs/houdini/nodes/sop/latticefromvolume.html | Hex-cell generation over a volume's active region with `ix`/`iy`/`iz` and `rest` attributes — the closest analogue to Crystallon Voxelize |
| Lattice Deform SOP | https://www.sidefx.com/docs/houdini/nodes/sop/lattice.html | Cage-based deformation; the lattice versus points control modes |
| Point Deform SOP | https://www.sidefx.com/docs/houdini/nodes/sop/pointdeform.html | Deformation by point cloud using connectivity for local transforms; avoids the Lattice SOP's rotation collapse |
| Volume Wrangle SOP | https://www.sidefx.com/docs/houdini/nodes/sop/volumewrangle.html | Running VEX per voxel over a VDB — the gyroid implicit function |
| cgwiki, Houdini Volumes | https://www.tokeru.com/cgwiki/HoudiniVolumes.html | VDB from Polygons to SDF workflow, VDB Resample, voxel-size-versus-count behaviour |

## Workflow references

| Workflow source | URL | Caveat |
|---|---|---|
| How to apply Gyroid lattice structure on any geometry in Grasshopper | https://youtu.be/o9br5xdym_o | **Title confirmed; transcript and description not retrievable.** The step sequence attributed to it in this collection is inferred from the plug-in set and from matching published tutorials, and is labelled as inference. |
| Cademy, POC Helmet & Gyroid Lattice Design tutorial | https://www.cademy.xyz/learn/poc-helmet-rhino-3d-modeling-gyroid-lattice-grasshopper-free-tutorial | The published Crystallon → wbThicken → Catmull-Clark → Dendro sequence, and the open-mesh vs closed-cell thicken rule |
| Parametric by Design, Prepare for 3D printing | https://parametricbydesign.com/grasshopper/tutorials/prepare-for-3d-printing/ | Voxel density versus computation guidance for Dendro |
