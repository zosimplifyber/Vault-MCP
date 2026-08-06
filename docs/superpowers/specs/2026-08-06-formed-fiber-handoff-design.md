# Formed Fiber: Design-to-Process Handoff Generator — Design

**Date:** 2026-08-06
**Status:** Approved, ready for implementation planning

## Problem

When a formed fiber part moves from design to production, the parameters
established during development — press, pressures, thicknesses, weights,
dryness — have to reach the process team that will run the mold. Today that
transfer happens on a Word document, `Simplifyber_Design-to-Process-Handoff`,
filled in by hand. Every field is retyped, including the ones the CAD system
already knows: the material of the pressed part, its computed mass and volume,
and the exact released filenames and revisions of the two files the process
team must pull.

Retyping known values is where the document goes wrong. A filename typed
without its revision, or a material transcribed from memory, sends the press
after the wrong geometry. The fields that genuinely require a human — the
measured process values — get no help either: the two machine pressures are
properties of the press, not of the part, and are re-entered identically on
every handoff for that machine.

This project builds a tool that fills what can be known automatically and asks
only for what cannot.

## Scope

**In scope**

- A new entry in the launcher's TOOLS panel that opens a handoff form
- Vault lookup of the general assembly and, via its CAD BOM, the final pressed
  part — pulling filename, revision, state and material
- Inventor lookup of the pressed part's computed mass and volume
- A new Part Volume row on the document, which the paper form does not have
- A machine profile library so section 1 is a dropdown pick
- Derivation of Standard Dry Weight from Bone Dry Weight
- Per-field target-vs-measured marking, as the document itself requires
- A reportlab rebuild of the document, saved into the local Vault workspace
  folder for the assembly

**Out of scope**

- Automatic check-in of the PDF to Vault. See "Why the PDF is not uploaded".
- Reading thicknesses, wet weight or dryness out of Inventor. Confirmed with
  the user that those live in development notes, not in the CAD files. Bone
  Dry Weight is the exception — see "Bone Dry Weight comes from Inventor".
- Editing the document layout without touching code. The layout is built in
  reportlab; changing it means changing Python.

## Decisions

Each of these was settled during brainstorming and is recorded with its
reasoning so implementation does not relitigate it.

### Where the process values come from

Most are typed. The user confirmed the thicknesses, wet weight and dryness
exist only in development notes, so scraping a development workbook was ruled
out and the tool's job for those is to minimise typing around values it cannot
source, not to source them.

Vault cannot supply them. The vault carries 125 property definitions; the
CAD-relevant ones are Material, Material Finish, Title, Description, Revision,
State, Project, Designer, Engineer, Vendor, Vendor Number, Source, Stock
Number and Cost. There is no mass, thickness, weight, pressure or machine
property in the vault at all.

### Bone Dry Weight and Part Volume come from Inventor

Bone Dry Weight is the one process value on the paper form that the CAD model
already knows: it is Inventor's computed mass for the pressed part, geometry
times the assigned material density. Part Volume is the geometry alone.

Part Volume is a **new row on the document** — the attached form has no volume
field. It is added at the user's request, printed in cm³, positioned directly
after Final Pressed Part Material because both describe the part rather than
the process. Inventor's database unit for volume is already cm³, so the value
is printed as read.

Because Vault has no Mass or Volume property, neither can come through the
REST call.
Two routes were considered. Adding a Vault user-defined property mapped to
Inventor's Mass would have been free at the tool end — the value would simply
appear in the CAD BOM response the design already fetches — but it needs a
Vault Settings change plus a re-index before existing files carry it. The user
chose the second route: read it directly from the part with Inventor COM,
which works today against unmodified files and is always current with the
model.

Both values are read from `MassProperties` rather than from the `Mass` and
`Volume` iProperty strings. The API reports database units — kilograms and
cubic centimetres — regardless of the document's display units, so grams is an
exact `* 1000` and cm³ needs no conversion at all. No unit parsing anywhere.

One caveat the implementation must not paper over: the computed mass equals
the bone dry weight only if the part's assigned material density is the dried
fibre density. The tool cannot verify that, so the field stays editable and
target-markable, and the form labels it as pulled from the model so a wrong
density is visible rather than silent. Volume is unaffected — it is geometry
alone and does not depend on the material.

### Standard Dry Weight formula

A standard dry part is 5% water **by mass of the finished standard dry part**,
so the bone dry fibre is the other 95%:

```
SDW = BDW / 0.95
```

The user confirmed this reading explicitly against the alternative (dry-basis
regain, `SDW = BDW * 1.05`, the textile convention). At 100 g bone dry the two
differ by 0.26 g, which is why it was worth confirming rather than assuming.
The implementation must carry the wet-basis reading in a comment so the next
reader does not "correct" it to the textile convention.

Dryness [%] stays typed. It is derivable as `BDW / wet weight * 100`, and that
was offered; the user chose to keep it a manual entry.

### Why the PDF is not uploaded to Vault

The user's first choice was to have the tool check the PDF into Vault. It
cannot, cheaply: `vault_rest_api.py` has no upload method because Vault REST
v2 has no file check-in endpoint at all. This is the same gap that forced
`scripts/vault_soap.py` into existence for lifecycle writes. Uploading would
mean a `FilestoreService` byte-stream upload plus `DocumentService.AddFile`
plus folder and category assignment — a new SOAP subsystem, verifiable only
against the live Vault, and dependent on permissions the API account may not
hold.

The agreed alternative gets most of the value for none of that risk: resolve
the assembly's Vault folder path, map it onto the local Vault workspace, and
write the PDF there. It appears in Vault Explorer as an uncontrolled file, and
one right-click adds it to Vault under the normal category and lifecycle.

## Architecture

Three new modules plus two edits, following the engine/GUI split the repo
already uses (`bom_purchasing.py` + `gui/purchasing.py`,
`scripts/check_file_properties.py` + `gui/file_property_check.py`).

| File | Role |
|---|---|
| `formed_fiber_handoff.py` | Engine. The `HandoffData` dataclass, machine-library loading, the Standard Dry Weight rule, output path and filename resolution. No Tk. |
| `formed_fiber_pdf.py` | reportlab renderer. `HandoffData` in, PDF on disk out, nothing else. |
| `machines.json` | Machine profiles. Sibling of `file_property_rules.json`, reloaded on every run. |
| `gui/formed_fiber_handoff.py` | The Tk form. Exposes `launch_gui(api, vault_id, cfg, parent)`. |
| `gui/launcher.py` | One `_tool_row` entry plus one `_on_open_formed_fiber_handoff` handler. |
| `scripts/inventor_automation.py` | Gains `read_part_physical_properties()`, plus an `open_visible` keyword on `open_document`. |

The renderer is split from the engine because it is the one piece with no
logic worth testing by assertion and the one most likely to churn on visual
feedback. Keeping it separate means layout tweaks cannot touch the
calculation rules.

`gui/__init__.py`'s docstring claims the package exposes "four Tk-based
desktop GUIs" and lists four; it is already stale at eight modules. Adding a
ninth is the moment to correct it.

## Data model

```python
@dataclass(frozen=True)
class Value:
    """One production value plus whether it is a target rather than measured."""
    text: str = ""
    is_target: bool = False


@dataclass
class HandoffData:
    # Section 1 — Machine and Process Details
    machine: str = ""
    vacuum_pressure: str = ""
    press_pressure: str = ""
    machine_characterized: bool = True

    # Section 2 — Production Details
    material: str = ""
    volume: str = ""               # cm³, from Inventor
    dry_thickness: Value = Value()
    wet_thickness: Value = Value()
    wet_weight: Value = Value()
    bone_dry_weight: Value = Value()
    standard_dry_weight: Value = Value()
    dryness: Value = Value()

    # Section 3 — File References
    ga_filename: str = ""          # rendered "CD-001659.iam (Rev 3)"
    part_filename: str = ""

    generated_on: date = field(default_factory=date.today)
```

Section 1 fields are plain strings: a machine is never a "target". Material and
Volume are plain strings for the same reason — one names a material, the other
is geometry read straight off the model, and neither is a measured quantity
with a target counterpart. The six remaining production fields are `Value` so
each can be marked. Section 3 fields arrive pre-rendered from Vault.

`Value` is frozen, so it is hashable and safe as a dataclass default.

## Field map

| Document field | Source |
|---|---|
| Machine – Brand and Model | `machines.json` dropdown |
| Vacuum Pressure [bar or barg] | Machine profile, overridable |
| Hot Press Pressing Pressure [bar] | Machine profile, overridable |
| Final Pressed Part Material | Vault `Material` on the pressed part |
| Part Volume [cm³] *(new row)* | Inventor computed volume, overridable |
| Dry Part Thickness [mm] | Typed, target-markable |
| Wet Part Thickness [mm] – Or Transfer GAPS | Typed, target-markable |
| Wet Weight [g] | Typed, target-markable |
| Bone Dry Weight [g] | Inventor computed mass, overridable, target-markable |
| Standard Dry Weight [g] | Computed from Bone Dry Weight, overridable |
| Dryness [%] | Typed, target-markable |
| General Assembly Filename | Vault file name + revision |
| Final Pressed Part Filename | Vault file name + revision |
| Date (footer) | Generation date |

Four typed values and one dropdown pick, against thirteen fields on the
document.

## Machine library

`machines.json` at the repo root, matching the shape and spirit of
`file_property_rules.json` — a leading `_comment` that explains itself, and
reloaded on every run so edits take effect without restarting.

```json
{
  "_comment": "Characterized presses ...",
  "machines": [
    {
      "name": "<brand and model, as it should appear on the document>",
      "vacuum_pressure": "<free text, e.g. '-0.9 barg'>",
      "press_pressure": "<free text, e.g. '120 bar'>",
      "characterized": true,
      "notes": "<optional, not rendered>"
    }
  ]
}
```

Pressures are strings, not numbers: the document's own unit is "bar or barg",
so the value carries its unit rather than assuming one.

`characterized: false` is meaningful. The document states that an
uncharacterized machine "must be characterized before the first production
run", so selecting one raises a visible warning in the form. It does not block
generation — a handoff for a not-yet-characterized press is a legitimate
document to produce.

Maintenance is by text editor, via an "Edit Machines" button that opens the
file in the default editor — the same treatment `gui/launcher.py`'s
`_on_edit_rules` gives `file_property_rules.json`. Presses change rarely
enough that a managed CRUD screen is not worth building.

## Vault integration

No new REST methods and no new BOM-walking logic. Both helpers already exist
in `scripts/check_file_properties.py` and are covered by
`tests/test_check_file_properties.py`:

- `fetch_file(api, vault_id, file_name)` → `{record, file_version_id, file_id,
  properties, note}`, where `properties` is flattened and keyed by display
  name (`"Material"`, `"Revision"`, `"State"`).
- `fetch_cad_children(api, vault_id, file_version_id)` → one entry per child in
  the CAD BOM, each already carrying its properties. `/uses` enriches every
  child in the same request when `prop_def_ids` is passed, so the whole BOM is
  one call regardless of size.

Both go through `option[propDefIds]`, which is the spelling Vault's *file*
endpoints require. The bare `propDefIds` that item endpoints accept returns
200 OK with the properties silently missing — this is why the tool must use
these helpers rather than calling the API directly.

The GA is chosen with `FileSearchDialog` from `gui/release_workflow.py`, not
the `SearchDialog` in `gui/search_dialog.py`. Both modules carry explicit "do
not merge them" notes: `SearchDialog` is item-based and returns a part number,
`FileSearchDialog` is file-based and returns a file name. Section 3 of the
document asks for filenames, so the file-based one is correct. It is consumed
by satisfying its duck-typed contract — the new GUI supplies `root`, `api`,
`vault_id`, `top_file_var`, `set_top_file`, `_brand_button` and
`_ensure_signed_in` — rather than by modifying either dialog.

### Filename rendering

The document asks for filenames "exactly as released, including revision", so
both render as `<file name> (Rev <revision>)`, e.g. `CD-001659.iam (Rev 3)`.

If either file's State is not `Released`, the form shows an amber note naming
the file and its state. Non-blocking: handoffs get drafted before release.

## Inventor integration

Reading the physical properties needs the part on disk, an Inventor instance,
and COM initialised on the calling thread. Each is a place this can fail, and
none of them may take the GUI down with it.

### Locating the file

The pressed part's local path is built the same way the output path is: its
Vault `Folder Path` (which `fetch_cad_children` already returns, since it
requests all properties) mapped onto `handoff.workspace_root`, joined with its
file name. The part's folder is not assumed to be the assembly's.

If the file is not there, nothing is read. The tool does not attempt a Vault
download — a file absent from the workspace has not been Get Latest'd, and
silently pulling a copy behind the user's back would produce numbers from a
version they are not looking at.

### Reading mass and volume

A new function in `scripts/inventor_automation.py`, returning **both values
from a single document open** — opening Inventor twice for two properties of
the same part would double the slowest step in the tool:

```python
@dataclass(frozen=True)
class PhysicalProperties:
    mass_g: float
    volume_cm3: float


def read_part_physical_properties(file_path: str | Path) -> PhysicalProperties:
    """Return the part's computed mass in grams and volume in cm³.

    Inventor's API reports database units (kilograms, cubic centimetres)
    regardless of the document's display units, so mass is an exact `* 1000`
    and volume needs no conversion.
    """
```

It reuses `get_inventor_app` and `open_document`, reads `Mass` and `Volume`
off `doc.ComponentDefinition.MassProperties`, and closes without saving. It
raises the module's existing `InventorUnavailableError` (no Inventor, no
pywin32) and `InventorAutomationError` (open failed, not a part document,
properties unavailable) — no new exception types.

`PhysicalProperties` is the module's first dataclass; the module currently
returns plain values. A two-field named result beats a bare tuple at the call
site, where mixing up mass and volume would otherwise be silent.

`open_document` currently hardcodes `Documents.Open(path, True)`, opening every
document visibly. A property read wants the document invisible: faster, and it
does not disturb whatever the user has open. The fix is an `open_visible: bool
= True` keyword that preserves today's behaviour for the release workflow, its
only current caller.

### COM on a worker thread

This is new ground for the module. Its only existing caller,
`scripts/release_workflow.py`, runs on the CLI's main thread, so COM
initialisation has never come up. The GUI does its lookups on a worker thread,
where COM must be initialised explicitly or every call fails with a
misleading error.

`read_part_physical_properties` therefore calls `pythoncom.CoInitialize()` on
entry and `CoUninitialize()` in a `finally`. Putting it inside the function
rather than in the GUI keeps the requirement next to the code that needs it,
and is harmless on the main thread where COM is already initialised.

### When it fails

Every failure is non-fatal and specific. Bone Dry Weight and Part Volume stay
manually editable and the form shows one line saying why: Inventor not
installed, pywin32 missing, the part not in the workspace, or the property
read failing. Standard Dry Weight simply has nothing to derive from until a
weight is typed. Because both values come from one call, they succeed or fail
together — there is no state where one is populated and the other is not.

## Output

**Filename** derives from the general assembly, dropping its extension:
`CD-001659.iam` → `CD-001659-DesignToProcessHandoff.pdf`. This follows
`bom_purchasing.py`'s `{assembly_number}-PurchasingExport.xlsx`.

**Folder** is the assembly's Vault folder mapped onto the local workspace.
Vault folder paths are `$`-rooted; the mapping replaces `$` with the workspace
root and converts separators:

```
$/DESIGNS/PRODUCTION EQUIPMENT/Mold 12
  → C:\Vault Workspace\DESIGNS\PRODUCTION EQUIPMENT\Mold 12
```

The root comes from a new `handoff.workspace_root` key in `config.json`,
defaulting to `C:\Vault Workspace` (the root already implied by
`bom_purchasing.DEFAULT_OUTPUT_DIR`). `config.json.example` gains the key.

If the mapped folder does not exist, the tool falls back to
`bom_purchasing.default_output_dir()` and reports the substitution in the
status bar. It does not create the folder — inventing a directory inside the
Vault workspace risks a path Vault does not know about.

The resolved destination is shown in an editable entry box before generation,
so any of this can be overridden by hand.

## PDF layout

A reportlab platypus rebuild of the attached document:

- **Header** — `Simplifyber_Logo.png` at left via
  `gui.theme._resource_path`, "Page 1 of 1" at right, horizontal rule beneath.
- **Title** — "FORMED FIBER" bold, ": DESIGN-TO-PROCESS HANDOFF" regular, both
  dark blue.
- **Intro paragraph** — the standing text from the document, verbatim.
- **Three sections** — numbered heading in dark blue with an underline rule,
  an italic grey lead-in paragraph, then a two-column PARAMETER/VALUE table:
  dark blue header row with white bold text, bold dark parameter names,
  alternating white and pale blue row banding, thin grey grid.
- **Footer** — `Date: <M/D/YYYY>`, `CONFIDENTIAL`, and the confidentiality
  notice in small italic grey.

Colours come from `gui/theme.py` (`DARK_BLUE = #1F3864`, `PALE_BLUE`,
`GRAY_BDR`, `DARK_GRAY`), converted to reportlab `HexColor`. Note that
`bom_purchasing.py` keeps its own copy of the same palette without the leading
`#` because openpyxl demands that form; the renderer uses `gui.theme` and the
two stay in sync by hand, as they already do.

Target-marked values render as `2.4 (TARGET)`. Empty values render as an em
dash, so a blank is visibly blank rather than ambiguous whitespace. If the
logo file or Pillow is missing, the header degrades to text, matching how the
GUIs already handle it.

## GUI

A `Toplevel` child of the launcher, styled with `gui/theme.py` like every
other tool, laid out in the document's own order so the form and the output
read the same way.

1. **General Assembly** — read-only filename box, "Find GA" button opening
   `FileSearchDialog`, plus revision and state labels.
2. **Final Pressed Part** — a table of the GA's CAD BOM children (file name,
   revision, state, material); selecting a row fills the part fields and
   kicks off the Inventor read on the worker thread, with a "Reading mass and
   volume from Inventor…" status line while it runs.
3. **Machine and Process Details** — machine dropdown, two pressure entries
   filled from the profile and editable.
4. **Production Details** — a Material row (from Vault) and a Part Volume row
   (from Inventor), both editable and neither with a checkbox, followed by six
   rows with a label, an entry and a "target" checkbox. Bone Dry Weight
   arrives pre-filled from Inventor and is labelled as read from the model.
   Standard Dry Weight recalculates as Bone Dry Weight changes and
   stops tracking once typed in directly; while it is tracking, its target
   checkbox mirrors Bone Dry Weight's, since a value derived from a target is
   itself a target. Overriding the value makes the checkbox independent too.
5. **Output** — destination entry, Generate button, "Open Folder" after a
   successful write.

Vault work runs on a worker thread with results posted back through a
`queue.Queue` drained via `root.after`, the threading shape every other GUI in
this package uses. No Tk call happens off the main thread.

## Error handling

| Condition | Behaviour |
|---|---|
| No Vault session | Tool opens. Find GA warns and offers sign-in; the three Vault-backed fields stay editable so a handoff can still be produced by hand. Output goes to the fallback folder. |
| `machines.json` missing or malformed | Empty dropdown, explanatory status line, pressure fields become free text. Never fatal. |
| Machine marked uncharacterized | Red warning quoting the document's rule. Generation proceeds. |
| GA has no CAD BOM children | Message saying so; the pressed-part fields stay manually editable. |
| Inventor or pywin32 missing | Status line naming which; Bone Dry Weight and Part Volume stay manually editable. |
| Pressed part not in the local workspace | Status line naming the path it looked for, so the fix (Get Latest) is obvious. No Vault download attempted. |
| Mass/volume read fails | Status line with the Inventor error; both fields stay manually editable. |
| Either file not Released | Amber note naming the file and state. Generation proceeds. |
| Blank fields at Generate | Warning listing them by name, with "generate anyway". Blanks render as em dashes. |
| Mapped workspace folder missing | Falls back to `bom_purchasing.default_output_dir()`, reports the substitution. |
| Destination not writable | Error naming the path; the form keeps its contents. |

## Testing

`tests/test_formed_fiber_handoff.py`, pytest, matching repo convention
(`pytest.ini`, fakes rather than live Vault calls — see
`tests/test_check_file_properties.py` for the API-fake pattern).

**Standard Dry Weight rule**
- `100` → `105.26`; `250` → `263.16` (locks the wet-basis reading against the
  `* 1.05` misreading)
- Blank input → blank output
- Non-numeric input → blank output, no exception
- Zero and negative input → blank output
- While tracking, marking Bone Dry Weight as a target marks Standard Dry
  Weight too; once overridden, the two flags move independently

**Machine library**
- Valid file loads every profile with its pressures
- Missing file → empty list, no exception
- Malformed JSON → empty list, no exception
- `characterized: false` surfaces on the loaded profile

**Inventor reader** — against a fake COM object, since no test may require
Inventor. `tests/test_check_file_properties.py`'s fake-API pattern is the
model.
- `Mass` of 0.10526 kg → `105.26` g (the `* 1000` conversion)
- `Volume` passes through unconverted
- A COM object that raises on open → `InventorAutomationError`, not a crash
- Missing pywin32 → `InventorUnavailableError`
- The document is opened invisibly and closed without saving

**Path and filename resolution**
- `$/A/B` + root → `<root>\A\B`
- The pressed part's own folder is used, not the assembly's
- Missing folder → fallback path
- `CD-001659.iam` → `CD-001659-DesignToProcessHandoff.pdf`
- Filename with no extension, and with a dot in the stem

**Rendering** — build a fully populated `HandoffData`, render to a tmp path,
then read it back with `pypdf` (already a dependency) and assert:
- all three section headings appear
- the Part Volume row appears with its cm³ unit
- every entered value appears
- a target-marked value appears with its `(TARGET)` suffix and an unmarked one
  does not
- a blank field renders the em dash
- rendering with no logo file present still produces a valid PDF

## Dependencies

`reportlab` and `pypdf` are already in `requirements.txt` for the watermark
tool, and `Pillow` is already optional-with-fallback in `gui/theme.py`.

`pywin32` is needed for the Inventor read and is **not currently declared**,
despite `scripts/inventor_automation.py` having required it since it was
written. It should be added to `requirements.txt` with a comment marking it
Windows-and-Inventor-only, alongside the existing optional entries like
`playwright`. Nothing about the declaration makes it mandatory at runtime —
the module already degrades cleanly when the import fails, and the tests fake
the COM layer — but an undeclared import that a shipped feature depends on is
worth fixing while we are here.

## Section 2 on the printed document

For the avoidance of doubt, Production Details prints eight rows in this
order:

1. Final Pressed Part Material
2. Part Volume [cm³] — new, not on the paper form
3. Dry Part Thickness [mm]
4. Wet Part Thickness [mm] – Or Transfer GAPS
5. Wet Weight [g]
6. Bone Dry Weight [g]
7. Standard Dry Weight [g]
8. Dryness [%]
