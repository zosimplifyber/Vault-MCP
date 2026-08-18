# Fyberite

**Material:** Fyberite
**Summary:** Thicker structural foam-formed sheet, ~1.5 mm pressed. Pulp/lyocell base with carbon fibre, CoPET and PLA.
**Source:** `Material Samples_Formulations & Testing.xlsx`, sheet(s) `Fyberite`, `Fyberite_Coating`
**Batch date:** July 8th

## Process conditions

| Material | Parameter | Value |
|---|---|---|
| Fyberite | Rinse | Yes |
| Fyberite | Press temperature | 180 C |
| Fyberite | Press dwell after water removed | ~10 s |
| Fyberite | Time to remove water | ~45 s |
| Fyberite | Press force | 20 kN |
| Fyberite | Target pressed thickness | 1.5 mm |
| Fyberite | Foamed | Yes |

Conditions as recorded in the sheet title: `Fyberite_Foam (Rinse, 180C, ~10 s after water removed [~45s], 20kN, Thickness = 1.5 mm)`

## Formulation

Batch basis: 640 g dry solids, 40000 g water, slurry consistency 1.602%.

Ingredients are listed in the order they are added to the slurry. Only ingredients actually dosed in this batch are shown; the source sheet also carries a standing list of zero-amount alternates.

| Material | Add order | Ingredient | Spec / supplier | Dry solids (g) | Stock conc. (%) | Dosed (g) | Water from stock (g) | % of solids | Handling |
|---|---|---|---|---|---|---|---|---|---|
| Fyberite | 1 | Carbon | Mixed Length (Vartega) | 96 | 100 | 96 | 0 | 15 |  |
| Fyberite | 2 | CMC |  | 0.96 | 5 | 19.2 | 18.24 | 0.15 | Mix Vigorously Until Minimal Bundles |
| Fyberite | 3 | Pulp | Hardwood (BEK) | 49.92 | 95 | 52.55 | 2.63 | 7.8 |  |
| Fyberite | 4 | Lyocell | 2MM | 160 | 92 | 173.91 | 13.91 | 25 |  |
| Fyberite | 5 | MFC | Exilva P | 12.8 | 10 | 128 | 115.2 | 2 | Mix Vigorously For 3 Min |
| Fyberite | 6 | PAE |  | 1.28 | 25 | 5.12 | 3.84 | 0.2 | Gentle Mix - 3 Min |
| Fyberite | 7 | PLA | 6MM Kilop | 96 | 100 | 96 | 0 | 15 |  |
| Fyberite | 8 | CoPET | 6MM (110C) | 224 | 100 | 224 | 0 | 35 |  |
| Fyberite | 9 | SDS |  | 16 | 10 | 160 | 144 | 7.14 | Foam to 2x volume |

Notes recorded with this batch:

- Slurry Consistency: 5/5

## Coating and finishing (sheet `Fyberite_Coating`)

| Material | Stage | Step | Detail |
|---|---|---|---|
| Fyberite | Coating and finishing | Rinse | Water Soak, Pass through Rollers (Repeat 2 Times) |
| Fyberite | Coating and finishing | Bath Coat (directly after Rinse/Roller) | Of Bath: 0.75% Shell, 5% Witcobond 363 // Of Coating: [Pigment not necessary, but if current bath has pigment then whatever color] 5% Pigment |
| Fyberite | Coating and finishing | Dry <100C | Oven Dry for 30 min (or until dry) |
| Fyberite | Coating and finishing | Spray Coat | 2 sprays (120 GSM total): 1st @ 50 GSM & dry till tack <100C (~1min), 2nd @ 70 GSM & dry at <100C (~5min); Of Bath: 30% DLU:202 (60:40) // Of Coating: 5% Pigment, 1% PP, 5% BK01 |
| Fyberite | Coating and finishing | Press | 100 psi, 180C, 1 min |
| Fyberite | Coating and finishing | Molds & Color (5x Finished Good Samples) | BMW Car (1x Each: Gray, Mother Yellow, Shoe Burgandy), SF Car (1x Each: Black), Pebble (1x Each: Black) |

## Test results

Percentages are computed from the paired before/after measurements: water absorption is `(post weight - weight) / weight`, thickness change is `(post thickness - thickness) / thickness`, and the drop columns are `(before - after) / before`. Precise per-sample values are also in `test_samples.csv`.

### Process step: finished

| Material | Process step | Sample | Rep | GSM | Thickness (mm) | Density (kg/m3) | Tensile (N) | Water absorption (%) |
|---|---|---|---|---|---|---|---|---|
| Fyberite | finished | SF_Fyberite_8.5_1.1 |  | 1660 | 2.44 | 680.33 |  | 40.2 |
| Fyberite | finished | SF_Fyberite_8.5_1.2 |  | 1726.67 | 2.43 | 710.56 |  | 39.6 |
| Fyberite | finished | SF_Fyberite_8.5_2 |  | 1366.67 | 2.11 | 647.71 | 1140.27 |  |
| Fyberite | finished | SF_Fyberite_8.5_3.1 |  | 1793.33 | 2.52 | 711.64 |  | 38.7 |
| Fyberite | finished | SF_Fyberite_8.5_3.2 |  | 1746.67 | 2.51 | 695.88 |  | 36.8 |
| Fyberite | finished | SF_Fyberite_8.5_4 |  | 1893.33 | 2.64 | 717.17 | 1772.06 |  |

### Process step: uncoated

| Material | Process step | Sample | Rep | GSM | Thickness (mm) | Density (kg/m3) | Bending peak force (N) |
|---|---|---|---|---|---|---|---|
| Fyberite | uncoated | FiberiteV2-2-2 | A | 1150 | 1.89 | 608.47 | 20.2 |
| Fyberite | uncoated | FiberiteV2-2-2 | B | 1223.63 | 1.94 | 630.74 | 24.28 |
| Fyberite | uncoated | FiberiteV2-2-2 | C | 1132.08 | 1.89 | 598.98 | 22.39 |
| Fyberite | uncoated | FiberiteV2-2-2 | D | 1097.77 | 1.99 | 551.64 | 37.38 |

### Process step: internal coating

| Material | Process step | Sample | Rep | GSM | Thickness (mm) | Density (kg/m3) | Tensile (N) | Bending peak force (N) | Flexural modulus (MPa) | Water absorption (%) | Tensile drop (%) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Fyberite | internal coating | SF_Fyberite (MF CF)-1-2 | A | 1600 | 2.87 | 557.49 |  | 30.93 | 740.3 | 19.5 |  |
| Fyberite | internal coating | SF_Fyberite (MF CF)-1-2 | B | 1597.33 | 2.64 | 605.05 | 1336.65 |  |  | 23.4 | 21.5 |
| Fyberite | internal coating | SF_Fyberite-1-5 | A | 1525 | 2.36 | 646.19 |  | 25.2 | 1243.89 | 21.3 |  |
| Fyberite | internal coating | SF_Fyberite-1-7 | B | 1762.5 | 2.77 | 636.28 |  | 33.66 | 1109.55 | 30.5 |  |
| Fyberite | internal coating | SF_Fyberite (MF CF+Starch)-2-4 | A | 1562.5 | 2.8 | 558.04 |  | 12.83 | 470.95 | 14.4 |  |
| Fyberite | internal coating | SF_Fyberite (MF CF+Starch)-2-4 | B | 1698.67 | 2.92 | 581.74 | 1040.24 |  |  | 13.7 | 71.5 |
| Fyberite | internal coating | SF_Fyberite (MF CF+Starch+SW)-3-4 | A | 1812.5 | 2.8 | 647.32 |  | 29.23 | 933.88 | 13.1 |  |
| Fyberite | internal coating | SF_Fyberite (MF CF+Starch+SW)-3-4 | B | 1712 | 2.82 | 607.09 | 968.41 |  |  | 12.9 | 49.7 |
| Fyberite | internal coating | SF_Fyberite-1-6-Vartega | A - H | 1525 | 2.09 | 729.67 |  | 47.4 | 2287 | 19.7 |  |
| Fyberite | internal coating | SF_Fyberite-1-6-Vartega | B - V | 1687.5 | 2.08 | 811.3 |  | 41.3 | 2323 | 17 |  |
