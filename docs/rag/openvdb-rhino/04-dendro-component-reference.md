# Dendro Component Reference

Source: DENDRO Voxel plug-in for Grasshopper, Documentation v0.01.00, ECR Labs (14-page PDF)
Source: Grasshopper Docs addon listing, https://grasshopperdocs.com/addons/dendro.html
Source: Dendro repository, https://github.com/ryein/dendro and https://github.com/ecrlabs/dendro
Compiled for Simplifyber engineering, August 2026.

Dendro is a volumetric modelling plug-in for Grasshopper built on OpenVDB. It wraps points,
curves and meshes as a volume type and provides boolean, smoothing, offset and morphing
operations on those volumes. It is licensed MPL-2.0 and is described by its authors as a
beta product.

The vendor's own framing of why it exists: "When working with meshes or Breps, these types
of operations are often computationally heavy, prone to failures, or cannot handle complex
geometry. OpenVDB's volume data structures allow for quicker computation with higher
repeatability." The design goal was that "Dendro makes working with volumes no different
than handling any other geometry in Grasshopper," avoiding the bounding-box thinking of
other voxel plug-ins.

## The 14 components

| Dendro component | Nickname | Category | What that Dendro component does |
|---|---|---|---|
| Create Settings (Dendro) | vSettings | Convert | Settings for converting geometry types to and from volumes. Feeds every other converter. |
| Mesh to Volume (Dendro) | vMesh | Convert | Creates a volume approximating a closed mesh. |
| Curve To Volume (Dendro) | vCurve | Convert | Creates a volume from a list of curves, wrapping each with a spherical profile of given radius. |
| Points To Volume (Dendro) | vPoints | Convert | Creates a volume from a point set, one sphere per point, with a single radius or a per-point radius list. |
| Volume to Mesh (Dendro) | mVolume | Convert | Creates a mesh approximating the volume. The only route back to native Grasshopper geometry. |
| Smooth Volume (Dendro) | vSmooth | Filters | Applies a smoothing filter to a volume. |
| Offset Volume (Dendro) | vOffset | Filters | Offsets the exterior boundary of a volume by a fixed world-unit distance. |
| Volume Blend (Dendro) | vBlend | Filters | Morphs between two volumes at a parameter t. |
| Create Mask (Dendro) | Mask | Filters | Creates a mask from a volume, to restrict where a filter takes effect. |
| Volume Union (Dendro) | vUnion | Intersect | Combines overlapping volumes into one body. |
| Volume Intersection (Dendro) | vInt | Intersect | Outputs the overlapping volumetric area of A and B. |
| Volume Difference (Dendro) | vDiff | Intersect | Subtracts B from A. |
| Read Volume (Dendro) | vRead | IO | Loads a `.vdb` file. |
| Write Volume (Dendro) | vWrite | IO | Saves a `.vdb` file. Requires a fully specified path ending in `.vdb` and a boolean trigger. |

## Create Settings — the four inputs

All Dendro converters read from a global settings parameter. The documentation is explicit
that "there are fringe use-cases where you may want to use different settings for each
converter, but in general, you should create one settings component and feed it into all of
your components."

| Create Settings input | Used by | Vendor description | Vendor suggestion |
|---|---|---|---|
| Voxel Size (S) in Dendro Create Settings | Mesh to Volume, Curve to Volume, Point Cloud to Volume | "the x, y, z dimensions of the individual voxels filling the volume. Think of this as the resolution of the volume." | "Keep this value as large as possible while working and decrease it as a final step." |
| Bandwidth (B) in Dendro Create Settings | Mesh to Volume, Curve to Volume, Point Cloud to Volume | "extends the available voxel field around your volume. Voxels within this band are set active, everything else is inactive." | "This controls the active voxel count so keep this value as small as possible in order to minimize computation time." |
| Isovalue (I) in Dendro Create Settings | Volume to Mesh | "the accuracy of the resulting mesh to the original value. it can be abstractly thought of as a positive or negative offset." | "Typically you want to keep this at zero to maintain accuracy to the actual volume. If you encounter 'Invalid Mesh' issues, then setting the Isovalue to 0.002 is often a workaround." |
| Adaptivity (A) in Dendro Create Settings | Volume to Mesh | "sets the uniformity of mesh faces. Values can range from 0-1, with a value 0 being more equalized and dense." | "Higher adaptivities will allow more variation in polygon size, resulting in fewer polygons and quicker calculations." |

There are two reasons beyond consistency to keep one Settings component: mismatched voxel
sizes force an extra resample inside every boolean, and mismatched settings make the
performance of a definition impossible to reason about.

## Converters

**Mesh to Volume** takes any closed mesh. The documentation warns that it "only works on
closed meshes. If a mesh is open you will get erratic results" — the illustrated failure is
a box missing one face producing a garbage volume. There is one useful exception: "You can
generate valid volumes from 'open' meshes if your voxel size is larger than any gaps in the
mesh," which makes it a mesh-repair and watertighting tool for 3D printing.

**Curve to Volume** wraps each curve with a spherical profile of a given radius, in Rhino
document units. The documentation calls out the lattice application directly: "Curves can
also be used to effectively create lattice structures from existing line data when used in
conjunction with other plug-ins such as Crystallon or Interlattice, potentially with better
reliability and performance characteristics than their built in mesh generators."

**Point Cloud to Volume** places a sphere at each point and accepts either one radius for
all points or a list of radii, one per point. This is the route to variable-thickness
volumes — the documentation's example runs a radius from 0.2 up to 3.0 along a curve. It
can also thicken a mesh or surface by feeding it the vertices.

**Volume to Mesh** accepts single or multiple Volume (V) inputs. Its behaviour is driven
entirely by the Isovalue and Adaptivity coming out of Create Settings.

## The invalid mesh problem

Volume to Mesh can emit an invalid mesh, and the documentation is candid that these "often
appear to output correctly and render from the Grasshopper canvas without issue but fail on
subsequent operations and will not bake. As a result, they can be frustrating to spot and
diagnose." The invalid state only shows when hovering the M output.

The documented fix order is: raise Isovalue from 0 to 0.001 or 0.002, which "will usually
result in a valid mesh output"; if that fails, tweak Adaptivity.

A community fix reported on the McNeel forum for the same symptom is Pufferfish's Rebuild
Mesh component with Merge Vertices enabled, described there as computationally efficient
compared with a C# degenerate-face cleanup.

## Filters

**Smooth Volume** inputs are Volume (V), Type (T), Width (W) and Iterations (I).

| Smooth Volume input | Meaning |
|---|---|
| Volume (V) on Dendro Smooth Volume | The input volume; single or multiple volumes. |
| Type (T) on Dendro Smooth Volume | Integer selecting the filter: Gaussian (0), Laplacian (1), Mean (2), Median (3). |
| Width (W) on Dendro Smooth Volume | "the scale of the smoothing effect", a positive integer. **Width has no effect on Laplacian type smoothing.** |
| Iterations (I) on Dendro Smooth Volume | The number of times the smoothing operation runs. |

The type choice is a real performance decision, not a taste decision — see
`02-openvdb-tools-and-their-cost.md`. On a true signed distance field, which is what
Mesh to Volume produces, Laplacian gives the same result as mean-curvature flow and is
documented by OpenVDB as "less computationally expensive". Gaussian is internally four
iterations of a separable mean filter, and Median is non-separable and "relatively slow".

**Offset Volume** offsets the exterior boundary by Distance (D) in document world units.
Positive and negative are both allowed, which the documentation notes is "very helpful in
shelling an object as well as making molds for casting." The cost of an offset scales with
distance divided by voxel size, because the underlying OpenVDB routine steps under a CFL
limit of half a voxel per iteration.

**Volume Blend** morphs between two volumes at parameter t over an interval 0 to 1. End
Time (E) sets the upper boundary of the time step "and is tied to voxel size." The vendor's
calibration procedure: attach both volumes, set t to 1, then raise End Time until the output
matches the B input exactly, then use t to blend.

**Mask Filter.** Smooth, Offset and Morph all take a Mask (M) input produced by Create
Mask. The mask is itself a volume defining the bounds of the effect, and must overlap the
target volume to do anything.

| Create Mask input | Meaning | Guidance |
|---|---|---|
| Volume (V) on Dendro Create Mask | The volume defining the mask area. It does not appear in the result. | Must overlap the target volume. |
| Min Value (A) on Dendro Create Mask | Defines how the mask effect transitions inside the masked boundary. | "should be a negative number. This value can not be zero." Start around -0.001 and work up. |
| Max Value (B) on Dendro Create Mask | Defines how the effect transitions outside the boundary. | Should be positive or zero; keep at 0 unless a specific effect is wanted. |
| Mask Invert (I) on Dendro Create Mask | Boolean choosing which side of the mask the filter affects. | False applies the filter inside the mask; True applies it outside. |

Masks are the tool for confining an expensive filter to a small region rather than paying
for it across every active voxel.

## Booleans

Union, Difference and Intersection. The vendor's claim: "Where mesh (and even NURBS)
booleans can be prone to failure, volume booleans are incredibly stable and can be
computationally lighter."

Union "must have multiple input volumes on the same branch of a tree in order to run
correctly" — a data tree structure requirement that silently produces wrong results if
violated. Intersect and Difference accept single volumes or lists; Difference subtracts B
from A.

## Grasshopper component compatibility

Dendro Volumes flow through most native Grasshopper components, but not all. The
documentation ships an explicit compatibility chart, because "voxel systems are not natively
supported by either Rhino or Grasshopper so some components can have unexpected and
unpredictable results."

| Native Grasshopper group | Status with Dendro Volume outputs |
|---|---|
| Sets — all List, Set and Tree items | Working with Dendro Volumes |
| Transform — Euclidean (Move, Rotate, Rotate 3D, Rotate Axis, Mirror, Orient, Move to Plane, Rotation Dir) | Working with Dendro Volumes |
| Transform — Array (Box, Curve, Linear, Polar, Rectangular) and Affine/Scale | Working with Dendro Volumes |
| Transform — Util (Compound, Split, Inverse Trans, Transform, Group, UnGroup) | Working with Dendro Volumes |
| Transform — Affine non-uniform (Scale NU, Shear, Shear Angle, Box Mapping, Camera Obscura) | **Not working** with Dendro Volumes |
| Transform — Morph group (all components) | **Not working** with Dendro Volumes |
| Transform — Array/Kaleidoscope, Affine/Project, Rect Mapping, Tri Mapping, Orient Direction | **Not working** with Dendro Volumes |
| Transform — Euclidean/Move Away From | Partially working with Dendro Volumes |
| Surface — Primitive / BBox, and Vector — Point / Project point | **Not working** with Dendro Volumes |

The working uniform-scale and array components matter for lattice work: a unit cell volume
can be arrayed as a volume rather than as thousands of curves, then unioned.

## Installation requirements

Three steps, and failing either of the last two "will result in the plug-in not working":

1. Download from `www.ecrlabs.com/dendro`.
2. Right-click the ZIP, Properties, and confirm **Unblock** — the file must not be blocked
   before extraction.
3. Copy all files to `C:\Users\[UserName]\AppData\Roaming\Grasshopper\Libraries\`.
4. In Rhino run `GrasshopperDeveloperSettings` and **uncheck** "Memory load *.GHA assemblies
   using COFF byte arrays".

## Build and dependencies

Dendro has a native C++ layer (DendroAPI) and a managed C# layer (DendroGH). OpenVDB and
its dependencies — Boost, CMake, c-blosc, TBB, zlib — are managed through a vcpkg manifest.
The current repository targets Rhino 8 by default with the RhinoCommon version configurable
through NuGet, and builds as x64 Release under Visual Studio 2022. macOS builds take the
dependencies from Homebrew.
