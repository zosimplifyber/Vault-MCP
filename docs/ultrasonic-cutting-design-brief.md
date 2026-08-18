# Ultrasonic Cutting of Simplifyber Nonwoven Mats — Design Brief

**Date:** 2026-08-07 (rev. B — updated with three peer-reviewed sources) · **Scope:** benchtop-first
system for (a) flat panel blanking and (b) trimming formed 3D parts · **Materials:** Fybron,
FyberCom, Fyberite (coated and uncoated)

> **Rev. B changes.** Three papers supplied on 2026-08-07 confirmed the two central claims below and
> **corrected three parameter recommendations** from rev. A. Traverse speed was too fast by 3–5×;
> "blade angle" conflated two distinct angles; horn material was over-specified to titanium. Two new
> first-order findings were added: wide-blade **amplitude uniformity** (§3a) and **through-thickness
> energy transfer** (§2a). Corrections are marked ⚠ throughout.

---

## 1. Material reality check

Ultrasonic cutting severs a sheet by driving a blade at 20–40 kHz into it; the local frictional and
hysteretic heating melts thermoplastic in the kerf, so the blade parts the material at a fraction of
the force a cold blade needs and the melt re-freezes as a sealed bead. **Everything depends on how
much of the sheet actually melts at the kerf**, and that is where the formulations need a second look.

There are two thresholds in the literature, and they are not the same number:

- **~50% or more** thermoplastic fibre to ultrasonically **cut and seal** a textile [US4560427].
- **60–65% or more** to ultrasonically **weld** one [Kara 2024].

All three families are ~50% "thermoplastic" by mass, so they sit at the cutting threshold and below
the welding one. But that 50% is split between fibres with very different melt points, and only the
low-melt fraction contributes to a seal before the cellulose scorches:

| Constituent | Fybron | FyberCom | Fyberite | Melt / behaviour | Seals? |
|---|---:|---:|---:|---|:--:|
| CoPET binder (110 °C) | 15% | 15% | 35% | flows well below cellulose char | **yes** |
| PLA (Kilop 6 mm) | — | — | 15% | ~150–170 °C | **yes** |
| Nylon 6,6 | 35% | 35% | — | ~255 °C — above cellulose char onset | marginal |
| Lyocell / viscose / pulp / hemp | 50% | 50% | ~33% | never melts; chars ~200–300 °C | no |
| Carbon fibre (Vartega) | — | — | 15% | inert, hard, **abrasive** | no |
| **Effective sealing fraction** | **15%** | **15%** | **50%** | | |

**This inverts the intuition.** Your own press schedule is the evidence: you consolidate Fybron at
160 °C and Fyberite at 180 °C, both far below nylon's 255 °C melt. The nylon is a reinforcing fibre in
your process, not a binder — and it will not become one under an ultrasonic blade either, because the
cellulose beside it chars first. So:

- **Fyberite** (CoPET 35% + PLA 15%) is the **strongest candidate** for true cut-and-seal, despite
  being the thickest, densest and hardest material — and despite the carbon.
- **Fybron / FyberCom** have only ~15% low-melt content. They sit well below the cut-and-seal window
  and should be expected to *sever cleanly but seal weakly*. Severing is still worth having; just do
  not specify a sealed edge until it is measured.

**A split melt range is itself a named difficulty.** Kara lists exactly three limitations on
ultrasonic weldability: high unit mass, high thickness, and *"fiber content with two different
melting temperatures"* [Kara 2024]. That study's PET:PA6 fibres melt ~30 °C apart and that was
already enough to complicate it. Your CoPET/nylon split is **145 °C apart**. All three limitations
apply to your material simultaneously, and more severely than in the published case.

---

## 2. These are not textile nonwovens — now quantified

The best available paper on heavy nonwovens is titled *"Ultrasonic weldability of thick and heavier
nonwoven fabrics."* Its heaviest sample is **227.9 gsm at 0.83 mm** [Kara 2024]. That is lighter than
your **lightest** material.

| | Spunbond (typical ultrasonic study) | Kara's "thick and heavier" | Simplifyber mats |
|---|---|---|---|
| Basis weight | 20–100 gsm | 108.9–227.9 gsm | **350–1,900 gsm** |
| Thickness | 0.1–0.5 mm | 0.39–0.83 mm | **0.65–2.9 mm** |
| Density | 50–200 kg/m³ | — | **430–910 kg/m³** |
| Thermoplastic | 100% | 100% (PET:PA6 bico) | **15–50% low-melt** |

Fyberite is **8× the basis weight and 3.5× the thickness** of the heaviest fabric in the heavy-nonwoven
literature, at a third of the meltable content. Treat all published nonwoven parameters as a lower
bound, not a guide, and design against the composite-trimming regime instead.

### 2a. ⚠ New concern: energy may not reach the far side

Kara's two heaviest fabrics — again, only 0.69 and 0.83 mm — produced seams whose *"face and back
sides were quite different, that proved the inefficient energy transfer of the welding cylinder for
these samples"* [Kara 2024]. Ultrasonic energy was already failing to penetrate sub-millimetre
material at 34.9 kHz.

At 0.65–2.9 mm and half the meltable content, expect this to be your dominant failure mode, not an
edge case. Three consequences:

1. **Use 20 kHz, not 28 or 35 kHz.** Lower frequency means higher amplitude and deeper penetration.
   This is now the single best-supported parameter choice in the brief.
2. **Expect asymmetric edge quality** — top face sealed, bottom face fibrous. Inspect *both* faces of
   every test cut. Judging a cut from one side will mislead you.
3. **Power must scale with thickness.** Kara needed only 125–200 W for 0.4–0.8 mm of *fully*
   thermoplastic fabric; Kim needed **1,200 W** for a 6.82 mm carbon preform [Kim 2024]. Your
   material sits between them, so the 500 W unit on hand is genuinely marginal for Fyberite.

---

## 3. Parameters to design to

| Parameter | Value | Source / rationale |
|---|---|---|
| Frequency | **20 kHz** for Fyberite and all coated stock; 28–40 kHz only for thin Fybron | Higher amplitude, deeper penetration; directly supports §2a. Patent art spans 15–100 kHz [US4560427, US5948208] |
| Amplitude | **30 µm** for wide-blade nonwoven work; **60 µm** for carbon-loaded Fyberite | Lee's 20 kHz wide-blade horn delivers 22–30.6 µm [Lee 2014]; Kim cut carbon preform at 60 µm [Kim 2024] |
| Amplitude (relative) | 70–100% of rated, default 100% | Below 70% not recommended without consultation [Dukane AN503] |
| Power | 500 W screening only; **1–2 kW** for production on Fyberite | 125–200 W for 0.8 mm fully-thermoplastic [Kara]; 1,200 W for 6.82 mm carbon [Kim] |
| ⚠ **Traverse speed** | **Start at 1 m/min.** Increase only while edge quality holds | **Corrected from rev. A (was 3–5 m/min).** Kim found 1 m/min gave zero damage; at 3 and 5 m/min the knife *"could not cut the preform plate effectively"* and tows were dislodged [Kim 2024]. Kara welded at 1.7–2.0 m/min [Kara 2024] |
| ⚠ **Blade point angle** | **24° carbide** for Fyberite; 30–55° steel acceptable for Fybron | **Rev. A conflated two angles.** This is the included angle of the edge itself. Kim used a 24° cemented-carbide cutter on carbon [Kim 2024]; Eastman's drag blades are 30/45/55° [Eastman] |
| ⚠ **Knife attack angle** | **45°** — the shallowest tested | Distinct from point angle: the angle between knife and workpiece. Feed force rises and thrust force falls as attack angle increases; 75° was worst [Kim 2024] |
| ⚠ **Horn material** | **Tool steel** for a bulky wide-blade horn; Ti-6Al-4V for slender high-cycle horns | **Corrected from rev. A (was Ti throughout).** Lee specifies tool steel (E 242.5 GPa, HRC 68) for stiffness and stability in a wide blade [Lee 2014] |
| Anvil geometry | Sealing surface **3–20° included, preferably 5–15°**; reinforcing surface ≥ 2.5° larger, ≤ 45°, preferably ~30° | Shallower sealing angle widens the seal band [US4542771] |
| Blade–anvil gap | **~0.003 in (76 µm)**, set then locked | Gap consistency dominates edge quality [US5265508] |
| Backing (free cutting) | **Silicone rubber** sacrificial plate; over-travel past sheet thickness | Kim backed a 6.82 mm preform with silicone rubber and cut to 9 mm depth [Kim 2024] |
| Ramp-up time | 0.150 s @ 20 kHz · 0.100 s @ 30 kHz · 0.050 s @ 40 kHz | Blade must **not** contact material during ramp-up [Dukane AN503] |
| Expected tolerance | **+250 to +365 µm oversize** on a programmed width, best case | Kim's best condition on carbon preform; treat as the floor, not the target [Kim 2024] |

### 3a. ⚠ New: a wide blade does not vibrate evenly

For panel blanking the natural tool is a **wide-blade horn**, which acts as the punch in a
punching/blanking system [Lee 2014]. Lee designed, tuned and measured one — and the result is a
warning:

- 20 kHz, 180 mm wide, tool steel, converter 18 µm in → **30.6 µm out (1:1.5 gain)**
- **Amplitude uniformity only 76%** — 22.0 µm minimum against 29.1 µm maximum along the edge
- Distribution is **M-shaped**: maxima about ⅔ out from each end, a minimum in the middle
- Fine slots (8 mm wide, 79 mm long, 62 mm apart in the tested horn) both **improve uniformity and
  radiate heat**, preventing hot spots
- Tuning is empirical: the fabricated horn came out 492 Hz off FEA; heat treatment dropped it 300 Hz;
  polishing the output face raised it 29 Hz

A 24% amplitude deficit at the middle of the blade matters enormously for material sitting *at* the
sealing threshold. On Fyberite the low spots may still seal; on Fybron, at ~15% low-melt, the
difference between 29 µm and 22 µm is plausibly the difference between a sealed edge and a furry one.

**Make amplitude uniformity a written spec.** Ask any wide-blade vendor for the measured
minimum-to-maximum ratio along the edge, and for slot geometry. Do not accept a nominal amplitude
figure alone.

---

## 4. Two configurations, because you have two jobs

**(a) Flat panel blanking — blade against an anvil ("cut and seal").**
The sheet is trapped between the vibrating blade and a hard anvil, which both reacts the load and
concentrates energy — that is what produces a sealed edge. Governed by anvil angle and gap
[US4542771, US5265508], and by blade amplitude uniformity (§3a).

**(b) 3D trim — free cutting, no anvil.**
No anvil can sit behind a formed part, so the blade slices unsupported onto a compliant sacrificial
backing. There is no sealed edge, the cut is purely severing, and blade attitude must stay tangent to
the path — hence the **7th axis** in trimming cells [US8277282], plus radial compliance to follow the
flash line as parts shrink. Kim's six-axis robot cut carbon preform successfully, but only inside a
narrow parameter window [Kim 2024].

---

## 5. What to get — three tiers

### Tier 0 — you already own the screening tool
**U.S. Solid 28 kHz / 500 W handheld, ~$995.** Enough to answer the go/no-go question this week at no
further cost. Its limits are real: handheld (no controlled force, speed or gap), fixed amplitude,
28 kHz is a compromise frequency, and 500 W is marginal for Fyberite per §2a. Treat it as a **material
screening instrument, not a process**. Add a rigid fixture, both a hard anvil *and* a silicone rubber
backing plate, a guide fence, fume extraction and hearing protection.

### Tier 1 — the real benchtop (recommended next purchase)
- **Ultrasonic tool head on a CNC flatbed** — the better route for 2D blanking. *Eastman* offers a
  20 kHz spindle with 30/45/55° drag blades on static-table and conveyor systems. One purchase gives
  controlled path, depth and speed, and the same table takes mechanical tools so ultrasonic can be
  benchmarked against them on identical geometry. **Zünd does not currently list an ultrasonic
  module** — its composite answer is the PRT/POT/EOT mechanical tools. Verify before comparing quotes.
- **Component stack, self-integrated.** 20 kHz generator (1–2 kW) + converter + booster + custom blade
  horn from *Dukane*, *Sonics & Materials*, *Branson/Emerson*, *Telsonic*, *Herrmann*, *Rinco* or
  *Sinaptec*. Cheaper in parts, but horn design is the hard part — Lee's paper shows a wide-blade horn
  needs FEA plus two physical tuning iterations to land within 21 Hz of target [Lee 2014]. Do not
  treat this as a purchasing exercise.

### Tier 2 — 3D trim cell (only after Tier 1 data)
6-axis robot + ultrasonic knife, compliant wrist, 7th axis for blade attitude. *Yaskawa Motoman*,
*Shape Process Automation*, *4D Systems*, *PushCorp*. Kim's setup — Stäubli RX160 + Sonotec HP-8701
(1,200 W, 24 kHz, 60 µm) + a 400 N force sensor — is a validated reference configuration [Kim 2024].

---

## 6. First test matrix

Run on the Tier 0 cutter. Response variables are ones you already measure, so results drop straight
into `Material Samples_Formulations & Testing.xlsx`:

| Factor | Levels |
|---|---|
| Material | Fybron (0.9 mm), FyberCom (2.2 mm), Fyberite (2.5 mm) |
| Condition | uncoated · internal-coated · coated + sprayed |
| Backing | hard anvil (cut-and-seal) · silicone rubber (free cut) |
| Traverse | **1 m/min baseline**, then 2 and 3 m/min |

Measure: seal presence and width **on both faces** (§2a) · **tensile and tear on a cut edge vs. a
die-cut control** · **elongation at break** — Kara saw strain collapse from 66% to 10–12% across a
weld line, and a stiff brittle edge may matter more than peak strength for a wearable [Kara 2024] ·
char/discolouration · blade deposit after 50 cuts · width error against nominal.

The single most informative early result is **Fyberite coated vs. uncoated** — you cut coated stock,
not greige, and nothing in the literature covers a PU/pigment surface over a fibre panel.

---

## 7. Risks

| Risk | Why | Mitigation |
|---|---|---|
| **Through-thickness energy transfer** | inefficient already at 0.83 mm in [Kara 2024]; your material is 1–3.5× thicker with half the meltable content | 20 kHz, 1–2 kW, inspect both faces; accept asymmetry or cut from both sides |
| **Amplitude non-uniformity** | 76% min/max along a 180 mm wide blade [Lee 2014] | specify measured uniformity and slot geometry; validate seal quality along the *whole* cut, not at one point |
| **Carbon abrasion** (Fyberite) | 15% Vartega carbon; CFRP is known for short tool life | cemented carbide, 24° point [Kim 2024]; budget blades as a consumable; track cuts-to-degradation |
| **Cellulose char** | 33–50% of every formulation cannot melt, only scorch | keep traverse up, amplitude high, dwell low; brown edge = too slow |
| **Weak seal on Fybron/FyberCom** | ~15% low-melt, below both published thresholds | do not promise a sealed edge; consider a CoPET-rich scrim or veil at cut lines |
| **Loss of extensibility** | strain fell from 66% to ~10–12% across ultrasonic seams [Kara 2024] | measure elongation, not just strength; a stiff edge may fail a wear test that tensile passes |
| **Fumes** | PLA, CoPET, PU coating and pigment decompose at the kerf | local extraction at the cut line; review coating SDS |
| **Noise** | 20 kHz sits at the edge of hearing and is loud in air | enclosure; OSHA 85 dBA 8-hr TWA as the hearing-conservation trigger |
| **Vendor data mismatch** | published work stops at 228 gsm / 0.83 mm | send *your* coated samples; refuse to buy on a demo of someone else's material |

---

## 8. Recommendation

1. **This week:** run §6 on the U.S. Solid cutter, starting at **1 m/min**. Cost: zero.
2. **On that data:** send Fyberite and Fybron to Eastman and to one stack vendor (Dukane or Sinaptec)
   for cut trials. Require *coated* stock, 20 kHz, and a report on **both** cut faces.
3. **Then buy Tier 1** — ultrasonic head on a CNC flatbed if 2D blanking dominates volume. Specify
   amplitude uniformity in the purchase order.
4. **Keep a mechanical control throughout.** If ultrasonic only severs Fybron without sealing it, a
   steel-rule die or oscillating knife may be cheaper and faster for that product. Ultrasonic earns
   its price on Fyberite — the sealed edge, the abrasive-material tool life, and the 3D trim path all
   point there.

---

## Sources

**Peer-reviewed (added rev. B, full text in the `Ultrasonic Cutting` RAG dataset)**

- **[Kara 2024]** Kara, S. *Ultrasonic weldability of thick and heavier nonwoven fabrics.* J. Appl.
  Polym. Sci. 141(33):e55840. doi:10.1002/app.55840. Five PET:PA6 bicomponent hydroentangled
  nonwovens, 108.9–227.9 gsm / 0.39–0.83 mm; 34,917 Hz; 125–200 W, 100–200 N, 1.7–2.0 m/min; 60
  sample types after ~110 pre-trials.
- **[Kim 2024]** Kim, H.G., Hong, T.H., Kim, D., Kim, S.H. *An experimental study of ultrasonic-knife
  cutting for a woven carbon fiber preform by an industrial robot.* Manufacturing Letters
  41:581–587 (NAMRC 52). Stäubli RX160 + Sonotec HP-8701, 1,200 W / 24 kHz / 60 µm, 24° cemented
  carbide; feed 1–5 m/min × attack angle 45–75°.
- **[Lee 2014]** Lee, C.H., Seo, J.S., Park, D.S. *Design and Tuning of the Ultrasonic Wide-blade Horn
  for Joining and Cutting Non-woven Fabrics.* Advanced Materials Research 941–944:1932–1936.
  doi:10.4028/www.scientific.net/AMR.941-944.1932. 20 kHz tool-steel wide-blade horn, 180 mm,
  final resonance 19,979 Hz, amplitude uniformity 76%.

**Patents (full text in the same dataset)**
US4259399 · US4496407 · US4542771 · US4560427 · US4596171 · US4610750 · US5061331 · US5265508 ·
US5785806 · US5948208 · US8277282 · US10654187

**Application notes:** Dukane AN503 (blade horn cutting), AN512 (stack scan / frequency lock)

**Vendor & technical references:**
[Telsonic](https://www.telsonic.com/en/cutting-with-ultrasonics/ultrasonic-cutting/) ·
[Dukane](https://www.dukane.com/markets-we-serve/ultrasonic-cutting) ·
[Eastman ultrasonic tool head](https://www.eastmancuts.com/products/ultrasonic-tool-head/) ·
[Zünd modules & tools](https://www.zund.com/en/cutting-systems/modules-and-tools) ·
[Sonobond nonwovens FAQ](https://sonobondultrasonics.com/faqs-ultrasonic-nonwovens-textiles-bonding/) ·
[Herrmann nonwovens](https://www.herrmannultraschall.com/en/welding-using-ultrasonics/joining-nonwovens-using-ultrasonics) ·
[Yaskawa Motoman](https://www.motoman.com/en-us/products/systems/ultrasonic-cutting) ·
[Shape Process Automation](https://shapeprocessautomation.com/solutions/ultrasonic-knife-deflashing/) ·
[Sinaptec](https://www.sinaptec.fr/en/ultrasonic-cutting/textile/) ·
[Sinosonics](https://www.sinosonics.com/knowledge-base/ultrasonic-cutting-complete-guide/)

**Internal:** `Material Samples_Formulations & Testing.xlsx` — formulation and test tabs for Fybron,
Fyberite, FyberCom (July 2026 runs)

**Still outstanding:** *A study of the ultrasonic-vibration cutting of carbon-fibre reinforced
plastics* (J. Mater. Process. Technol. 1994); the U.S. Solid unit's manual (amplitude and blade
specs are unpublished); the Witcobond coating SDS.
