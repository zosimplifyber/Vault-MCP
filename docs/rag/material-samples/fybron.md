# Fybron

**Material:** Fybron
**Summary:** Thin foam-formed nonwoven, ~0.9 mm pressed. Lyocell/viscose/nylon blend with CoPET as the thermobinder.
**Source:** `Material Samples_Formulations & Testing.xlsx`, sheet(s) `Fybron`, `Fybron_Coating`
**Batch date:** July 7th

## Process conditions

| Material | Parameter | Value |
|---|---|---|
| Fybron | Rinse | Yes |
| Fybron | Press temperature | 160 C |
| Fybron | Press dwell after water removed | ~10 s |
| Fybron | Time to remove water | ~30 s |
| Fybron | Press force | 10 kN |
| Fybron | Target pressed thickness | 0.9 mm |
| Fybron | Foamed | Yes |

Conditions as recorded in the sheet title: `Fybron_Foam (Rinse, 160C, ~10 s after water removed [~30s], 10kN, Thickness = 0.9 mm)`

## Formulation

Batch basis: 400 g dry solids, 40000 g water, slurry consistency 1%.

Ingredients are listed in the order they are added to the slurry. Only ingredients actually dosed in this batch are shown; the source sheet also carries a standing list of zero-amount alternates.

| Material | Add order | Ingredient | Spec / supplier | Dry solids (g) | Stock conc. (%) | Dosed (g) | Water from stock (g) | % of solids | Handling |
|---|---|---|---|---|---|---|---|---|---|
| Fybron | 1 | Lyocell | 5 MM | 40 | 92 | 43.48 | 3.48 | 10 |  |
| Fybron | 2 | viscose | 12 MM High-Tenacity (Minifibers: RATCD-015NRH-1200) | 160 | 91 | 175.82 | 15.82 | 40 |  |
| Fybron | 3 | CoPET | 6 MM | 60 | 100 | 60 | 0 | 15 |  |
| Fybron | 4 | Nylon | 24 MM (Minifibers: NYT66-0294RH-2500) | 17.5 | 96 | 18.23 | 0.73 | 4.38 |  |
| Fybron | 5 | Nylon | 18 MM (Minifibers: NYT66-0294RH-1900) | 35 | 96 | 36.46 | 1.46 | 8.75 |  |
| Fybron | 6 | Nylon | 12 MM (Minifibers: NYT66-0302LR-1200) | 70 | 96 | 72.92 | 2.92 | 17.5 |  |
| Fybron | 7 | Nylon | 6 MM (Minifibers: NYT66-0302LR-0600) | 17.5 | 96 | 18.23 | 0.73 | 4.38 | Mix till no fiber bundles |
| Fybron | 8 | SDS |  | 16 | 10 | 160 | 144 | 4 | Foam until 2x Volume |

Notes recorded with this batch:

- Foam Consistency: 5/5

## Coating and finishing (sheet `Fybron_Coating`)

| Material | Stage | Step | Detail |
|---|---|---|---|
| Fybron | Coating and finishing | Rinse | Water Soak, Pass through Rollers (Repeat 2 Times, Samples were not Rinsed in Simpliformer) |
| Fybron | Coating and finishing | Bath Coat (directly after Rinse/Roller) | Of Bath: 0.75% Shell, 5% Witcobond 363 // Of Coating: 5% Pigment |
| Fybron | Coating and finishing | Dry <100C | Oven Dry for 30 min (or until dry) |
| Fybron | Coating and finishing | Spray Coat | 2 sprays (120 GSM total): 1st @ 50 GSM & dry till tack <100C (~1min), 2nd @ 70 GSM & dry at <100C (~5min); Of Bath: 30% DLU:202 (60:40) // Of Coating: 5% Pigment, 1% PP, 5% BK01 |
| Fybron | Coating and finishing | Press | 100 psi, 155C, 1 min |
| Fybron | Coating and finishing | Molds & Color (5x Finished Good Samples) | BMW Car (1x Each: Gray, Mother Yellow, Shoe Burgandy), SF Car (1x Each: Black), Pebble (1x Each: Black) |

## Test results

Percentages are computed from the paired before/after measurements: water absorption is `(post weight - weight) / weight`, thickness change is `(post thickness - thickness) / thickness`, and the drop columns are `(before - after) / before`. Precise per-sample values are also in `test_samples.csv`.

### Process step: coated

| Material | Process step | Sample | Rep | GSM | Thickness (mm) | Density (kg/m3) | Tensile (N) | Tear (N) |
|---|---|---|---|---|---|---|---|---|
| Fybron | coated | SF-Fybron-Nylon-2-3 | A | 500 | 0.93 | 537.63 | 232.17 | 44.13 |
| Fybron | coated | SF-Fybron-PET-1-4 | B | 533.33 | 0.91 | 586.08 | 194.17 | 33.45 |

### Process step: internal coating

| Material | Process step | Sample | Rep | GSM | Thickness (mm) | Density (kg/m3) | Tensile (N) | Tear (N) | Water absorption (%) | Tensile drop (%) | Tear drop (%) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Fybron | internal coating | SF_Fybron PET-1-2 | A | 386.67 | 0.83 | 465.86 |  | 25.09 | 125.9 |  | -6.8 |
| Fybron | internal coating | SF_Fybron PET-1-2 | B | 373.33 | 0.87 | 429.12 | 317.98 |  | 135.7 | 59.1 |  |
| Fybron | internal coating | SF_Fybron Nylon-2-2 | A | 353.33 | 0.73 | 484.02 |  | 40.88 | 137.7 |  | 48 |
| Fybron | internal coating | SF_Fybron Nylon-2-2 | B | 346.67 | 0.67 | 517.41 | 298.37 |  | 123.1 | 61.1 |  |
| Fybron | internal coating | SF_Fybron PET-1-3 | A | 406.67 | 0.94 | 432.62 |  | 33.87 | 116.4 |  | 58.9 |
| Fybron | internal coating | SF_Fybron PET-1-3 | B | 384 | 0.89 | 431.46 | 230.46 |  | 134.7 | 42.3 |  |
| Fybron | internal coating | 100% PET | D | 353.33 | 0.65 | 543.59 |  | 60.56 | 50.9 |  | 6.1 |

### Process step: internal and spray

| Material | Process step | Sample | Rep | GSM | Thickness (mm) | Density (kg/m3) | Tensile (N) | Tear (N) | Water absorption (%) | Tensile drop (%) | Tear drop (%) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Fybron | internal and spray | SF_Fybron Nylon-2-4 | A | 493.33 | 0.98 | 503.4 |  | 46.73 | 128.4 |  | 49.6 |
| Fybron | internal and spray | SF_Fybron Nylon-2-4 | B | 509.33 | 0.93 | 547.67 | 235.11 |  | 112 | 48.6 |  |
| Fybron | internal and spray | SF_Fybron Nylon 2% AKD | C | 600 | 0.95 | 631.58 |  | 59.36 | 78.9 |  | 45.3 |

### Process step: finished

| Material | Process step | Sample | Rep | GSM | Thickness (mm) | Density (kg/m3) | Tensile (N) | Tear (N) | Water absorption (%) | Tensile drop (%) | Tear drop (%) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Fybron | finished | SF_Fybron_8.5_1.1 |  | 493.33 | 0.82 | 601.63 |  | 33.73 | 83.8 |  | 12.7 |
| Fybron | finished | SF_Fybron_8.5_1.2 |  | 509.33 | 0.91 | 559.71 | 310.87 |  | 97.4 | 59.4 |  |
| Fybron | finished | SF_Fybron_8.5_2.1 |  | 620 | 0.95 | 652.63 |  | 47 | 80.6 |  | 33.6 |
| Fybron | finished | SF_Fybron_8.5_2.2 |  | 690.67 | 1.01 | 683.83 | 403.55 |  | 62.5 | 47 |  |
| Fybron | finished | 90:10 Viscose_1.1 |  | 666.67 | 0.86 | 775.19 |  | 63.82 | 31 |  | -34.4 |
| Fybron | finished | 90:10 Viscose_1.2 |  | 784 | 0.86 | 911.63 | 472.93 |  | 24.5 | -1.2 |  |
| Fybron | finished | 90:10 Viscose_2.1 |  | 720 | 0.9 | 800 |  | 69.5 | 25 |  | -8.3 |
| Fybron | finished | 90:10 Viscose_2.2 |  | 765.33 | 0.89 | 859.93 | 474.15 |  | 20.2 | 0.9 |  |

## Data notes

- These samples appear in the results above because they were tested on the Fybron sheet, but no formulation sheet defines them, so their recipes are unknown: `100% PET`, `90:10 Viscose_1.1`, `90:10 Viscose_1.2`, `90:10 Viscose_2.1`, `90:10 Viscose_2.2`.
