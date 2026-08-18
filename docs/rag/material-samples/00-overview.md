# Simplifyber material samples: overview

**Source:** `Material Samples_Formulations & Testing.xlsx`

This collection describes Simplifyber's foam-formed nonwoven material samples: what goes into each slurry, how it is pressed and coated, and how the pressed samples performed in testing.

## Materials

| Material | Description |
|---|---|
| Fybron | Thin foam-formed nonwoven, ~0.9 mm pressed. Lyocell/viscose/nylon blend with CoPET as the thermobinder. |
| Fybron_Nylon | Fybron variant that swaps the CoPET binder line for a CoPET/nylon binder and drops the rinse step. |
| Fyberite | Thicker structural foam-formed sheet, ~1.5 mm pressed. Pulp/lyocell base with carbon fibre, CoPET and PLA. |
| Fyberite_v2 | Higher-pulp, lower-consistency Fyberite revision run at 0.4% consistency with Exilva MFC-F. |
| Fyberite_NoFoam | Fyberite run without foaming (no SDS) at 0.4% consistency. |
| FyberCom | Composite construction: a Fyberite structural base (~1.5 mm) with a soft Fybron-style skin from this recipe (~0.7 mm), pressed together to ~2.2 mm. |
| FyberCom_Dyed | FyberCom with a Fyberite_v2 base (~1.6 mm) and food dye added to the skin slurry. Pressed to ~2.3 mm. |
| FybeRoll | Coating-only product. The substrate is purchased FiberTex PET roll goods rather than a formed slurry, so there is no formulation sheet. |

## How the documents fit together

- One document per material, each holding its process conditions, slurry formulation, coating recipe and test results.
- `Materials consumption by week` logs total fibre and chemical usage.
- `test_samples.csv` holds all 40 individual test samples as flat rows, one row per sample, for precise numeric lookup.
- `Data quality notes` lists every gap and inconsistency found in the source workbook.

## Reading the process conditions

Every formulation sheet records its press conditions in one line, for example `Fyberite_Foam (Rinse, 180C, ~10 s after water removed [~45s], 20kN, Thickness = 1.5 mm)`. That means: the formed sheet is rinsed, the press runs at 180 C, water takes about 45 s to be removed, the press is then held about 10 s longer, at 20 kN, to a target thickness of 1.5 mm.

## Reading the formulation tables

- **Dry solids (g)** is the ingredient's oven-dry mass in the batch.
- **Stock conc. (%)** is the solids content of the material as supplied, so a 92% lyocell needs `dry / 0.92` grams dosed.
- **Dosed (g)** is what is actually weighed out; **water from stock** is the moisture that comes along with it and counts toward the batch water.
- **% of solids** is the ingredient's share of total dry solids - the number to compare across formulations.
- **Add order** is the sequence the ingredients go into the mixer, which matters for dispersion.

## Reading the test results

- **GSM** is grams per square metre; **density** is GSM divided by thickness.
- **Tensile (N)** and **tear (N)** are peak forces, not normalised stresses.
- `Post` columns are measured after a water soak. **Water absorption %** is the mass gain, so 126% means the sample took on 1.26x its own dry mass in water.
- **Drop %** columns are the loss in a property after soaking: `(before - after) / before`. A negative drop means the property improved.
- **Process step** distinguishes `uncoated`, `internal coating` (bath coat only), `internal and spray`, `coated`, and `finished` samples. Compare like with like.
