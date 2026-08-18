# Rhino 8, OpenVDB and the Plug-in Environment

Source: McNeel forum, "OpenVDB incompatibility", https://discourse.mcneel.com/t/openvdb-incompatibility/193906
Source: McNeel forum, "Are Open VDBs Rhino's new geometry type?", https://discourse.mcneel.com/t/are-open-vdbs-rhinos-new-geometry-type-vdb-to-native-machine-tool-paths/189999
Source: Rhino ShrinkWrap command help, http://docs.mcneel.com/rhino/8/help/en-us/commands/shrinkwrap.htm
Source: Rhino ShrinkWrap feature page, https://www.rhino3d.com/en/features/shrinkwrap/
Source: McNeel forum, "Using Dendro with Grasshopper C# — how do developers handle OpenVDB?", https://discourse.mcneel.com/t/using-dendro-with-grasshopper-c-how-do-developers-handle-openvdb/214146
Compiled for Simplifyber engineering, August 2026.

## Rhino 8 ships its own OpenVDB

`OpenVDB.dll` is included in Rhino 8, and ShrinkWrap uses signed distance functions built on
it. Nathan Letwory of McNeel stated the version directly: **"The actual version in use is
10.0.0."**

This has two consequences pulling in opposite directions.

## The conflict

Mariusz Hermansdorfer reported that a plug-in leveraging OpenVDB as a native library worked
in Rhino 7 and broke in Rhino 8: **"all works well in R7 but R8 uses its own version of the
OpenVDB.DLL apparently incompatible with my wrapper."**

Two OpenVDB builds cannot generally coexist in one process under the same symbol names.
OpenVDB namespaces its ABI by version (`openvdb::v10_0` and so on), but the loader resolving
`OpenVDB.dll` to Rhino's copy rather than the plug-in's is enough to break a wrapper built
against a different version.

Dale Fugier's recommended workaround: **"You can always build OpenVDB with a different
output name."** Hermansdorfer confirmed he would test that approach.

**Practical reading for a Dendro deployment:** if Dendro misbehaves in Rhino 8 in ways it did
not in Rhino 7 — loading failures, crashes inside volumetric components, inconsistent
results — a DLL resolution conflict with Rhino's bundled OpenVDB 10.0.0 is a live
hypothesis, not an exotic one. Check which `OpenVDB.dll` the process has actually loaded
before debugging the definition.

There is no supported route for a plug-in to *share* Rhino's internal OpenVDB. On the C#
thread, "no Rhino-internal OpenVDB shortcut was confirmed"; developers building against
Dendro are expected to build OpenVDB and its dependencies through the supplied vcpkg
manifest.

## The opportunity — ShrinkWrap as a native OpenVDB path

The same bundled library is exposed through `ShrinkWrap`, which creates a watertight mesh
around open or closed meshes, NURBS geometry, SubD and point clouds. It is available as a
Rhino command and as a Grasshopper component in Rhino 8.

Its documented use cases overlap heavily with what Dendro's Mesh to Volume →
Volume to Mesh round trip is often used for: meshes for 3D printing, solid union meshes from
multiple objects, solid meshes from scan fragments, meshes without internal
self-intersections, offset meshes for shelling, and valid closed meshes from broken or
hard-to-repair geometry.

Where that is the actual job, ShrinkWrap is native, threaded, involves no plug-in DLL
conflict, and has no version-compatibility surface at all.

Where it is *not* a substitute: ShrinkWrap has no boolean operations, no mask-limited
filtering, no morphing, no `.vdb` file interchange, and no way to hold a volume as a
first-class object across several operations. Those remain Dendro's territory.

Full ShrinkWrap parameter table is in `08-gyroid-workflow-and-faster-alternatives.md`.

## Dendro versions and repositories

There are two GitHub repositories in circulation for the same project, `ryein/dendro` and
`ecrlabs/dendro`. The official documentation directs users to `github.com/ecrlabs/dendro`
and to `www.ecrlabs.com/dendro` for downloads. The current source targets **Rhino 8 by
default**, with the RhinoCommon version adjustable through NuGet, and builds as x64 Release
under Visual Studio 2022. Dependencies — OpenVDB, Boost, CMake, c-blosc, TBB, zlib — come
from a vcpkg manifest. Licence is MPL-2.0.

The vendor describes Dendro as "a beta product being actively updated", so version currency
is worth checking before diagnosing any behaviour as a bug.

## Installation gotchas that present as "the plug-in does not work"

Both of these produce a silently absent plug-in rather than an error, and both are called
out in the official documentation as fatal:

1. **The downloaded ZIP must be unblocked before extraction.** Right-click the ZIP,
   Properties, General tab, confirm Unblock. Unblocking the extracted files afterwards is
   not equivalent.
2. **COFF byte array loading must be off.** Run `GrasshopperDeveloperSettings` in Rhino and
   uncheck "Memory load *.GHA assemblies using COFF byte arrays". This setting is what
   prevents a `.gha` with native dependencies from resolving them.

Files go to `C:\Users\[UserName]\AppData\Roaming\Grasshopper\Libraries\`.

## Referencing Dendro from code

A `.gha` is a renamed `.dll`, so `dendrogh.gha` renamed to `dendrogh.dll` can be referenced
from Visual Studio — though recompiling from the repository is the recommended route.
Boolean operations are callable directly through the Volume classes. One developer on the
thread integrated the Dendro source directly into their own plug-in to avoid separate DLL
management, which also sidesteps the version-alignment problem.

Reported benefit: Laurent Delrieu writes, "I use it like that and it is faster than using
Component from Dendro", because the visualisation overhead of the canvas components is
eliminated.

Version alignment with the installed Grasshopper and Rhino remains necessary either way.

## Hardware and Grasshopper threading

The general McNeel forum guidance for heavy definitions is unchanged and unglamorous: lock
the solver before making multiple parameter changes, use a Data Dam to hold upstream
updates, and expect single-thread performance to dominate because the Grasshopper solver
itself is single-threaded even where individual components are not.

The split matters for hardware choice. Crystallon's cluster-based lattice generation is
pure single-threaded Grasshopper and rewards high single-core clock speed. Dendro's
volumetric operations and `volumeToMesh` are TBB-threaded and reward core count. A
definition dominated by the former will not get faster on a many-core machine.
