# Digital CNC Cutting Systems for Nonwovens — Zünd and Eastman

Sources:
- Zünd modules and tools. https://www.zund.com/en/cutting-systems/modules-and-tools
- Zünd specialty industries. https://www.zund.com/en/applications/specialty-industries
- Eastman Machine, composites. https://www.eastmancuts.com/industries/composites/

**URL correction, August 2026:** the two Zünd URLs carried in earlier notes,
`zund.com/en/applications/technical-textiles` and `zund.com/en/applications/composites`,
both now return HTTP 404. Zünd has restructured its site. Technical textiles and composite
content now sits under `applications/specialty-industries`, and the tooling detail under
`cutting-systems/modules-and-tools`.

## Why digital knife cutting is the default candidate here

Single-ply CNC knife cutting is chemistry-indifferent, gives full contour freedom, needs
no per-shape tooling, and produces a clean mechanical edge. For a non-thermoplastic web it
loses nothing relative to ultrasonic, because there was no seal to lose. Its weak point is
material hold-down, not cutting.

## Zünd tool modules

Cutting-relevant modules. Zünd states cutting depths for some tools and not others.

| Zünd tool module | Zünd code | What that Zünd module is for | Depth or capacity stated by Zünd |
|---|---|---|---|
| Zünd Universal Cutting Tool | UCT | Universal drag-knife cutting | Materials up to 5 mm thick |
| Zünd Electric Oscillating Tool | EOT | High-frequency oscillation for soft to medium-density materials | Not stated |
| Zünd Electric Oscillating Tool 250 | EOT-250 | High-performance electric motor for thick cardboard and leather | Not stated |
| Zünd Pneumatic Oscillating Tool | POT | Extended stroke for tough, dense materials | Up to 110 mm / 4.3 in thick |
| Zünd Driven Rotary Tool | DRT | Fabrics and technical textiles at high processing speed | Not stated |
| Zünd Kiss-Cut Tool | KCT | Adjustable-pressure kiss-cutting, mainly vinyl | Not stated |
| Zünd Scoring Cutting Tool | SCT | Combined scoring and cutting of cardboard | Up to 5 mm thick |
| Zünd Press Cutting Tool | PCT | Cutting and compressing in one step | Not stated |
| Zünd V-Cutting Tools | VCT1, VCT2 | Multi-angle bevel cuts in foamcore and display board | Not stated |
| Zünd Perforating Tool | PTT1 | Precise perforations | Not stated |

Router modules RM-L (3.6 kW spindle), RM-A and RM-S (1 kW class) exist for aluminium,
wood, plastics and composites. Zünd's Q-Line carries its own module set: AUTOMO, UNITO,
DRAWTO, ELOSTO, CRETO 24/61/150, PERTO, VUTO, PRESTO.

**The two modules to ask about for a nonwoven mat are POT and DRT.** POT for a lofty or
thick consolidated sheet where stroke depth matters, DRT for speed on a thinner web.
The Press Cutting Tool, PCT, is worth raising as well — cutting and compressing in one
step could be relevant to a lofty foam-formed sheet, but Zünd publishes nothing about it
on non-thermoplastic fibre.

Zünd's stated material span in the specialty-industries area includes "lightweight textile
structures," "carbon, glass, and aramid fibers," and "flexible tool options for rubber,
honeycomb boards, foams, or technical fabrics," plus "engineering plastics, conductive
foams, laminates, films, and insulating papers."

## Eastman Machine systems

| Eastman cutting system | Type of Eastman system | What that Eastman system is for |
|---|---|---|
| Eastman C135 | Conveyor cutting system | Continuous cutting and conveying, InMotion™ software, extra-long patterns |
| Eastman S135 | Static table cutting system | Cutting, marking and punching, with firm material hold-down for a clean edge |
| Eastman Talon | Multi-ply cutting system | Stacked material, industrial fabrics and technical textiles |
| Eastman Blue Jay | End cutting system | Spreading and cutting, pull-off clamp and guided knife, straight-line cuts |
| Eastman Buzzaird® | Pneumatic shear | Air-powered, custom blade shapes |
| Eastman Chickadee® | Handheld knife | Lightweight, more power and capacity than a standard handheld |
| Eastman Hornet | Cordless handheld knife | Rechargeable, variable speed, high torque |

Eastman's stated composite materials are "glass fiber, carbon fiber, and aramid in both dry
and prepreg forms." Documented demonstration materials include vinylester hybrid prepreg,
TeXtreme fabric, carbon tooling quadraxial and fibreglass core.

Eastman's S135 hold-down claim and the Talon multi-ply capability are the two things worth
testing against a porous low-density web.

## Vacuum hold-down — the real constraint on a porous web

**Vacuum hold-down, not cutting force, is the binding constraint when knife-cutting a
porous nonwoven.**

The vacuum system has to be sized for the **air permeability** of the material, not for the
bed area. A porous, low-density nonwoven leaks air continuously across its whole surface,
so a bed sized by area for a solid sheet will not hold a permeable web flat. The web lifts,
shifts under the knife, or drags with the blade, and contour accuracy collapses.

The standard mitigation is a low-permeability underlay or a masking film over or under the
material, which is often necessary on lightweight material. That adds a consumable and a
removal step.

Neither vendor publishes vacuum sizing guidance for nonwovens on the pages reviewed.

## The gap in both vendors' published data

**Neither Zünd nor Eastman publishes tool-to-material matching charts or feed rates for
nonwovens.** Zünd gives depth capacity for UCT, POT and SCT but no feed rates and no
material matching. Eastman gives no table dimensions, no vacuum specifications, and lists
no sample-cutting service on the page reviewed.

Both companies run application labs and will cut submitted samples. That is the route to
the numbers.

## What to request from an application lab

When sending samples to Zünd or Eastman, specify basis weight, thickness, density, and
whether the sheet is consolidated or lofty.

Request back:

1. Tool module used, by code
2. Blade part number
3. Feed rate
4. Cutting depth setting
5. Number of passes
6. Vacuum configuration, including any underlay or masking film
7. The cut samples themselves, for edge inspection
