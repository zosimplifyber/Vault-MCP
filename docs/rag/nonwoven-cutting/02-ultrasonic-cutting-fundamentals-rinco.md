# Ultrasonic Cutting Fundamentals — Rinco Ultrasonics

Source: Rinco Ultrasonics, ultrasonic equipment manufacturer.
https://www.rincoultrasonics.com/ultrasonic-knowledge/ultrasonic-cutting/

## Mechanism of ultrasonic cutting

Rinco's own description: "With ultrasonic cutting, the oscillating motion and the melting
of the material allow the ultrasonic blade to glide almost effortlessly through the
product."

The blade separates the material by **plastification**, not by pressure. Ultrasonic
oscillation at the blade tip heats the contact zone, the material softens, and the blade
passes through against greatly reduced resistance.

**This is the load-bearing point for material selection.** The low-cutting-force advantage
of ultrasonic cutting is a consequence of the material melting. On a fibre that does not
melt, the mechanism degrades to a vibrating knife: it still cuts, but the force advantage
shrinks and the edge abrades rather than sealing.

## Sonotrode and anvil geometry for ultrasonic cutting

| Ultrasonic cutting hardware item | Specification stated by Rinco |
|---|---|
| Sonotrode form for ultrasonic cutting | Sharp blade configuration, referred to as a cutting sonotrode |
| Maximum sonotrode width for ultrasonic cutting | Up to 530 mm wide, quoted for a food product sonotrode |
| Anvil material for ultrasonic cutting | Glass, or a steel sheet |
| Anvil hardness limit for ultrasonic cutting | Maximum 55 HRC (Rockwell C) |

**The 55 HRC anvil limit is a hard constraint.** Rinco states the product "can be laid on
a mount made from glass or a steel sheet with a maximum hardness of 55 Rockwell (HRC)."
An anvil harder than 55 HRC damages the blade tip. This governs anvil and support-bed
specification for any blade-based ultrasonic installation.

## Cutting speeds for ultrasonic cutting

| Ultrasonic cutting application | Speed stated by Rinco |
|---|---|
| Continuous ultrasonic cutting of textiles | 25 metres per minute |
| Ultrasonic cutting of textile labels | Up to 120 cuts per minute |

Rinco qualifies the 25 m/min figure as an average guideline that requires
application-specific testing.

## Materials Rinco lists for ultrasonic cutting

- Food products: cakes, cheese, frozen items, creamy substances
- Technical textiles, nonwovens, fabrics
- Plastics: PE, PP, PVC films
- Rubber and elastomers

## Material requirement — the thermoplastic qualification

Rinco qualifies its textile category as **thermoplastic-component textiles**, stating that
textiles with a "thermoplastic component only require minimal cutting pressure."

Rinco does not state a blanket thermoplastic requirement for every material class it
lists, but the low-pressure claim is attached specifically to the thermoplastic case. For
a cellulosic or otherwise non-melting nonwoven, the minimal-cutting-pressure claim should
not be assumed to carry over.

## Advantages Rinco claims for ultrasonic cutting

Precision, minimal waste, edge sealing, and low maintenance. Rinco does not enumerate
limitations on this page.

## Tuned parameters in blade-based ultrasonic cutting

Blade-based ultrasonic cutting exposes four interacting parameters that must be tuned
together for each material:

1. Amplitude at the blade tip
2. Applied pressure of the blade into the material
3. Cutting speed along the cut path
4. Blade angle relative to the material

Resonance tuning is a per-material setup step, not a one-time machine calibration. See
`03-ultrasonic-cutting-primer-acme-mills.md` for how the feedback control maintains it.
