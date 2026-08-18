# Nonwoven Mat Cutting — Collection Index

Scope: industrial methods for cutting nonwoven mat and consolidated fibre sheet, the
equipment vendors behind each method, and the process parameters each one exposes.
Compiled for Simplifyber engineering, August 2026.

These are structured technical summaries written from vendor and academic sources, with
the source URL on every document. They are not scraped article text.

## The central finding

The published nonwoven cutting literature is written almost entirely for **thermoplastic
webs** — polypropylene spunbond and meltblown for hygiene and medical products. This
matters because the two methods that produce a sealed, fray-free edge in a single pass,
ultrasonic and laser, both depend on the fibre melting and re-solidifying.

For a cellulose-based or otherwise non-thermoplastic nonwoven, such as a foam-formed or
water-formed sheet:

| Cutting method | Behaviour of the method on a non-melting, non-thermoplastic web |
|---|---|
| Ultrasonic cutting on a non-melting web | Still cuts, but the low-cutting-resistance advantage is reduced, because that advantage comes from plastification of the fibre. The edge abrades rather than seals. |
| Laser cutting on a non-melting web | Chars the cellulose rather than fusing it. |
| Rotary, razor, oscillating knife and die cutting on a non-melting web | Indifferent to fibre chemistry. These work normally and give a clean mechanical edge, but no seal. |
| Waterjet cutting on a non-melting web | Heat-free and viable, but the part comes out wet and must be dried. |

**Consequence for the design:** edge stabilisation becomes a separate downstream
operation rather than a free byproduct of the cut. That is the design decision this
research surfaces.

### How firmly this conclusion is sourced

Rinco explicitly qualifies its textile category as thermoplastic-component textiles.
SinapTec states outright that its ultrasonic textile cutting is for thermoplastic
textiles or nonwovens containing thermoplastic fibres. Dukane's rotary system is
explicitly melt-and-separate — the anvil "causes materials to melt away."

Acme Mills lists "natural and synthetic fibers" without qualifying the thermoplastic
requirement. Treat that as an omission rather than as evidence to the contrary, because
the mechanism Acme Mills itself describes is thermal softening: "oscillations heat and
soften materials for cutting."

## Documents in this collection

| Document file in the nonwoven mat cutting collection | Source organisation | What that document covers |
|---|---|---|
| `00-index.md` | — | Collection index, the method comparison table, and the central thermoplastic finding |
| `01-cutting-methods-overview-sollex.md` | Sollex, industrial blade manufacturer | All six mechanical and thermal methods, blade part numbers and dimensions, method selection by thickness |
| `02-ultrasonic-cutting-fundamentals-rinco.md` | Rinco Ultrasonics, equipment maker | Ultrasonic mechanism, sonotrode and anvil geometry, the 55 HRC anvil limit, cutting speeds |
| `03-ultrasonic-cutting-primer-acme-mills.md` | Acme Mills, converter, plus SinapTec | 20–40 kHz range, resonance tuning and feedback control, system components, application fit |
| `04-rotary-ultrasonic-cut-and-seal-dukane.md` | Dukane, equipment maker | Rotary cut-and-seal, the patterned anvil, 2700 fpm, the basis-weight economic argument |
| `05-waterjet-cutting-textiles.md` | TechniWaterjet | Pure waterjet without abrasive — the heat-free option for non-melting webs |
| `06-digital-cnc-cutting-systems.md` | Zünd, Eastman | Single-ply digital cutting, tool modules, vacuum hold-down on a porous web |
| `07-peer-reviewed-references.md` | Academic literature | Four papers, with a coverage-gap note |

## Open questions that need vendor trials, not more searching

1. **Digital cutting.** Neither Zünd nor Eastman publishes tool-to-material matching
   charts or feed rates for nonwovens. Both run application labs and will cut submitted
   samples.
2. **Waterjet on nonwovens.** Unknown: kerf width at pure-waterjet pressure; whether the
   jet blows through or distorts a low-basis-weight web; what backing or vacuum support
   is needed; wetting depth and drying time; dimensional recovery after wet-then-dry;
   and whether plies can be stacked without delamination or edge taper. Dimensional
   recovery is the one to watch for a water-formed or foam-formed sheet, which may
   re-swell.
3. **No published parameter study exists** for cutting non-thermoplastic cellulosic or
   foam-formed webs. The process window has to be established experimentally.

## Suggested next actions

1. Send material samples to the **Zünd** and **Eastman** application labs. Specify basis
   weight, thickness, density, and whether the sheet is consolidated or lofty. Request
   back: tool module, blade part number, feed rate, cutting depth, number of passes,
   vacuum configuration, and the cut samples themselves for edge inspection.
2. Get a **waterjet trial** on the same samples, targeting the questions above.
3. Search the **paper and board converting** literature rather than the textile
   literature. Die cutting and rotary cutting of paperboard and moulded fibre is a much
   closer physical analogue to a cellulosic web than PP nonwovens are.
4. Investigate **edge stabilisation by binder application** rather than by thermal
   fusion.
