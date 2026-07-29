# Publish BOM Deliverables — Per-Part Selection Design

**Date:** 2026-07-29
**Status:** Approved, ready for implementation planning
**Extends:** `docs/superpowers/specs/2026-07-28-publish-bom-deliverables-design.md`

## Problem

The tool queues jobs for everything the scan resolves. That is right for the
common case — publish the whole BOM — but there is no way to say "just these
three parts" or "STEP only, skip the drawings".

Today the only way to narrow the run is to edit the BOM export, which is both
laborious and wrong: the BOM is a record of the assembly, not a work order.

The original design considered row checkboxes and deliberately chose the
simpler Scan-then-Submit flow. Use has since shown the narrower case is
common enough to want. This spec adds selection without disturbing the
two-click "publish everything" path.

## Scope

**In scope**

- Two master toggles for output type: PDF drawings, STEP files
- A checkbox per part row
- Five bulk controls: All, None, Invert, Missing drawing, Both files
- A live line showing what Submit will actually queue
- Selection preserved across a re-scan of the same BOM

**Out of scope**

- Any change to scanning, job params, or the fire-and-forget model
- Per-part output type (a row cannot ask for PDF while another asks for STEP;
  type is global). Rejected as more control than the workflow needs.
- Saving a selection between sessions

## Decisions

| Decision | Choice | Rationale |
| --- | --- | --- |
| Granularity | Global type toggles + per-part checkbox | "STEP only, these six parts" is two clicks plus six |
| Default after scan | Everything ticked, both types on | Preserves today's two-click publish-everything flow |
| Bulk controls | All, None, Invert, Missing drawing, Both files | Both semantic ones map to real questions: what needs attention, what is fully publishable |
| Re-scan | Preserve ticks by part number | Re-scan usually follows a fix in Vault; losing a hand-built selection to one click is punishing |
| New parts on re-scan | Arrive ticked | A part that just appeared should not be silently excluded |
| Retarget BOM path | Clear everything | Unchanged from today — a different BOM invalidates the scan |
| Checkbox mechanism | Glyph column in the Treeview | Standard Tk idiom, no dependencies, keeps multi-select |
| Glyph | ASCII `[x]` / `[ ]` | Unicode ballot boxes render inconsistently across Windows fonts; the table already uses ASCII hyphens for this reason |
| Selection storage | Set of stems | Makes re-scan preservation fall out; stems are already the deduped unique key |
| Where selection lives | GUI only | `ScanRow` describes what is in Vault, not what the user clicked |

## Layout

```
BOM file      [ CD-001608 BOM.xlsx        ] [Browse...]
Top assembly  [ CD-001608                 ]  blank=skip
Generate:     [x] PDF drawings   [x] STEP files      [ Scan ]  [ Submit ]
Select:  [ All ] [ None ] [ Invert ] [ Missing drawing ] [ Both files ]
--------------------------------------------------------------------------
      Part       Description           Model       Drawing     Status
 [x]  CD-001612  bmw vacuum deckle...  ...612.ipt  ...612.idw  2 jobs
 [ ]  CD-001578  bmw vacuum screen...  ...578.ipt  --          STEP only - no drawing
 [x]  CD-001613  bmw kft90 vacuum...   ...613.iam  --          STEP only - no drawing
--------------------------------------------------------------------------
10 part(s) - 10 model(s) - 3 drawing(s) - 13 job(s) - 7 missing a drawing
Queueing 9 job(s): 3 PDF + 6 STEP  (6 of 10 parts)
```

Two summary lines, deliberately separate. The first states what is in Vault —
scan facts, unchanged from today. The second states what is about to happen —
live, following the ticks. Merging them invites reading one as the other.

## Interaction

- Clicking the `[x]` cell toggles that row. `identify_column` and
  `identify_row` scope the handler to the check column, so clicking any other
  cell still selects the row normally for reading.
- Spacebar toggles every currently selected row. Treeview's shift-click and
  ctrl-click ranges therefore become bulk toggles at no cost: drag, then
  space.
- `Missing drawing` and `Both files` **replace** the selection rather than
  adding to it: after either, the ticked set is exactly the matching rows and
  nothing else. They answer a question ("which parts need attention?"), and a
  filter that silently unioned with whatever was already ticked would not
  answer it. `Missing drawing` matches rows whose status begins with
  `STEP only - no drawing`; `Both files` matches rows with a resolved model
  *and* a resolved drawing.
- The two type toggles are independent of the row ticks. Turning off
  `PDF drawings` does not untick any row; it removes PDF jobs from the run,
  which the "Queueing" line reflects immediately.
- Submit is disabled whenever the effective job count is zero — nothing
  ticked, or both type toggles off. This composes with the existing rule that
  Submit is disabled until a successful scan and re-disabled after submitting.

Prefix matching, not equality, on the status check: a status carries a
`(multiple matches)` suffix when a stem was ambiguous, and an equality
comparison would silently skip exactly the rows most worth a human's eye.

## A job is queued only when all three hold

1. its row is ticked, and
2. the type toggle for its kind is on, and
3. the file exists in Vault (a resolved version id)

Nothing about (3) changes; it is the existing scan result. A ticked row whose
part has no drawing simply contributes no PDF job, as it does today.

## Selection state and the re-scan merge

Selection is a `set[str]` of stems held by the dialog.

The merge on re-scan, given the previous selection, the previous scan's stems,
and the new scan's stems:

| Stem was | and was | becomes |
| --- | --- | --- |
| present before | ticked | ticked |
| present before | unticked | unticked |
| not present before | — | ticked |
| gone now | either | dropped |

This is pure set logic over stems, so it lives in `publish_bom.py` as a
testable function rather than inside a widget callback — it is the fiddliest
part of this change and the part most worth a test.

## Engine changes

Four, all small and all backward compatible.

**1. `_planned_jobs(row, *, include_pdf=True, include_step=True)`**

The single place that decides what jobs a row implies. Defaults preserve
current behavior exactly.

**2. `submit_jobs(..., *, include_pdf=True, include_step=True)`**

Passes the flags through. The GUI hands it only the ticked rows, so selection
never enters the data model.

**3. `count_planned_jobs(rows, *, include_pdf, include_step) -> dict`**

Returns `{"pdf": n, "step": n, "total": n}` for the "Queueing…" line, and is
**implemented by calling `_planned_jobs`** rather than re-deriving the rule.

This is not incidental. The defect found in the final review of the original
build was precisely this shape: a count computed one way, the behavior another,
and the two drifting apart with nothing to catch it. A displayed count that can
disagree with what Submit does is the same defect in different clothing. One
rule, one function, and the label is a consequence of it.

**4. `merge_selection(previous, previous_stems, new_stems) -> set[str]`**

Pure set logic for the table above.

## Testing

Added to `tests/test_publish_bom.py`:

- `_planned_jobs` honors each flag independently; both off yields no jobs
- `submit_jobs(include_pdf=False)` submits only STEP job types
- `submit_jobs(include_step=False)` submits only PDF job types
- Both flags off: `api.submitted` stays empty and the queue check is not even
  attempted
- Default arguments still queue both kinds — pins backward compatibility for
  every existing caller
- `count_planned_jobs` agrees with what `submit_jobs` actually submitted,
  across the flag combinations. This is the test that would have caught the
  earlier counting bug, so it is written in that shape deliberately.
- `merge_selection`: ticked-and-present stays ticked; unticked-and-present
  stays unticked; a new stem arrives ticked; a vanished stem is dropped and
  does not leak into the result

The GUI stays unit-untested, per repo convention, but is driven headlessly
before the work is called done: toggling a cell, spacebar over a multi-row
selection, each of the five bulk buttons, the live "Queueing" line, Submit
disabling at zero effective jobs, and selection surviving a simulated re-scan.

## What does not change

Fire-and-forget submission, always-republish, `BOM Structure` authoritative,
the scan itself, every job param shape, and the existing summary line. This
change is additive: with everything ticked and both toggles on — the state a
scan lands in — the tool behaves exactly as it does today.

## Open questions

None.
