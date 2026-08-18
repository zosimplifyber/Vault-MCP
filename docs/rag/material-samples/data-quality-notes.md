# Data quality notes

**Source:** `Material Samples_Formulations & Testing.xlsx`

Everything below was found while reshaping the workbook. Nothing here has been silently corrected in the source; where a value could not be trusted it was left blank and recorded here.

## Derived percentages were re-checked

Every water absorption, thickness change and property-drop percentage was recomputed from the before/after pair on its own row and compared with the value the sheet stored. All but four reconciled. The four that did not are listed under the per-sheet findings below and have been left blank rather than carried through, because each was produced by a formula reading a cell that was never filled in: Excel treats the blank as zero, which turns an unmeasured sample into an apparent 100% loss of strength. Those are not real results.

## Structural issues

- Sheet `Fyberite_v2` carries the title `Fyberite_NoFoam (No Rinse, 180C, ...)`, which looks like a copy-paste artefact from the `Fyberite_NoFoam` sheet. The two sheets have different formulations, so the title on `Fyberite_v2` is probably wrong rather than the data.
- Sheet `Testing_Fybron` contains six `SF_Fyberite_8.5_*` samples. They have been attributed to Fyberite, not Fybron.
- Sheet `Testing_Fybron` tests `90:10 Viscose` and `100% PET` samples. No formulation sheet defines either, so their recipes are unknown.
- On `Testing_Fybron`, the A/B/C/D group at rows 22-25 mixes four different formulations (`SF_Fybron Nylon-2-4`, `SF_Fybron Nylon 2% AKD`, `100% PET`). The Average and SD rows the sheet computes across that group therefore average unlike samples.
- Sheet `Testing_FyberCom` is an empty template. Its column list is the planned FyberCom test matrix: tensile, tear, elongation, abrasion, Bally flex, vamp flex, cleanability, water absorption, dimension change and weathering resistance.
- Batch dates are recorded inconsistently: some as text (`July 7th`) with no year, some as real dates (2026-07-15). The text dates are assumed to be 2026 to match.

## Per-sheet findings

### `Fybron`

- row 4 (Lyocell) has a broken formula in the dosing columns; those cells are left blank

### `Fybron_Nylon`

- row 4 (Lyocell) has a broken formula in the dosing columns; those cells are left blank

### `FyberCom_Dyed`

- row 13 (Food Dye) has a broken formula in the dosing columns; those cells are left blank

### `Testing_Fybron`

- sample '100% PET' names no material family and no formulation sheet defines it; it is grouped under Fybron because that is where it was recorded
- sample '90:10 Viscose_1.1' names no material family and no formulation sheet defines it; it is grouped under Fybron because that is where it was recorded
- sample '90:10 Viscose_1.2' names no material family and no formulation sheet defines it; it is grouped under Fybron because that is where it was recorded
- sample '90:10 Viscose_2.1' names no material family and no formulation sheet defines it; it is grouped under Fybron because that is where it was recorded
- sample '90:10 Viscose_2.2' names no material family and no formulation sheet defines it; it is grouped under Fybron because that is where it was recorded
- row 43 column 'Tensile Drop, %' holds #DIV/0!; left blank
- row 43 column 'Tear drop, %' holds #DIV/0!; left blank
- sample 'SF_Fyberite_8.5_1.1' is recorded on the Fybron sheet but names Fyberite; it is attributed to Fyberite
- row 44 (SF_Fyberite_8.5_1.2) reports tensile_drop_pct = 9.9% but tensile_n is blank, so the sheet computed it against an empty cell; dropped
- row 44 column 'Tear drop, %' holds #DIV/0!; left blank
- sample 'SF_Fyberite_8.5_1.2' is recorded on the Fybron sheet but names Fyberite; it is attributed to Fyberite
- row 45 (SF_Fyberite_8.5_2) reports tensile_drop_pct = 100% but post_tensile_n is blank, so the sheet computed it against an empty cell; dropped
- row 45 column 'Tear drop, %' holds #DIV/0!; left blank
- sample 'SF_Fyberite_8.5_2' is recorded on the Fybron sheet but names Fyberite; it is attributed to Fyberite
- row 46 column 'Tensile Drop, %' holds #DIV/0!; left blank
- row 46 column 'Tear drop, %' holds #DIV/0!; left blank
- sample 'SF_Fyberite_8.5_3.1' is recorded on the Fybron sheet but names Fyberite; it is attributed to Fyberite
- row 47 (SF_Fyberite_8.5_3.2) reports tensile_drop_pct = 51.7% but tensile_n is blank, so the sheet computed it against an empty cell; dropped
- row 47 column 'Tear drop, %' holds #DIV/0!; left blank
- sample 'SF_Fyberite_8.5_3.2' is recorded on the Fybron sheet but names Fyberite; it is attributed to Fyberite
- row 48 (SF_Fyberite_8.5_4) reports tensile_drop_pct = 100% but post_tensile_n is blank, so the sheet computed it against an empty cell; dropped
- row 48 column 'Tear drop, %' holds #DIV/0!; left blank
- sample 'SF_Fyberite_8.5_4' is recorded on the Fybron sheet but names Fyberite; it is attributed to Fyberite
