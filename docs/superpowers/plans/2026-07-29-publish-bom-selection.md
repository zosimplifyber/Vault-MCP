# Publish BOM Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user choose which parts get published and whether PDFs, STEPs, or both are generated, without disturbing the existing two-click publish-everything flow.

**Architecture:** Three small pure additions to the engine (`_planned_jobs` gains filter flags, `count_planned_jobs` counts by calling it, `merge_selection` handles re-scan), then a Treeview checkbox column and control strip in the dialog. Selection lives in the GUI as a set of part stems; it never enters `ScanRow`, which stays a description of what is in Vault rather than of what the user clicked.

**Tech Stack:** Python 3, `tkinter`/`ttk`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-07-29-publish-bom-selection-design.md`

**Baseline:** `tests/test_publish_bom.py` → 52 passed. Full suite → 376 passed, 2 skipped (skips are display-dependent Tk tests and flake between skipped and passed; ignore them). `publish_bom.py` is 553 lines, `gui/publish_bom.py` is 371.

---

## Background an engineer needs

**This change is additive.** A fresh scan lands with every part ticked and both
type toggles on, which reproduces today's behavior exactly. Every new engine
parameter defaults to the permissive value so existing callers and tests are
untouched.

**Why `count_planned_jobs` calls `_planned_jobs` instead of re-deriving the
rule.** A previous review of this module found `summarize()` counting one way
while the code behaved another — the display said zero gaps while the rows
plainly had them. A "Queueing N jobs" label that can disagree with what Submit
actually queues is the identical defect. There must be exactly one function
that decides what a row implies, and the label must be a consequence of it.

**Status strings must be matched by prefix, not equality.** A status carries a
`" (multiple matches)"` suffix when a stem resolved to more than one file.
`row.status == STATUS_MODEL_ONLY` silently skips exactly the rows most worth a
human's attention. Use `.startswith(...)`.

**Stems are unique.** `load_publish_rows` dedupes by stem and `scan_bom`
refuses to append a top assembly already present, so a stem identifies exactly
one row. That is what lets the Treeview use the stem as the row `iid`.

---

## File Structure

| File | Change |
| --- | --- |
| `publish_bom.py` (modify) | `_planned_jobs` flags; `submit_jobs` flags; new `count_planned_jobs`; new `merge_selection` |
| `gui/publish_bom.py` (modify) | Checkbox column, type toggles, bulk buttons, queue line, submit wiring |
| `tests/test_publish_bom.py` (modify) | ~12 new engine tests |

---

## Task 1: Engine — filter flags on `_planned_jobs` and `submit_jobs`

**Files:**
- Modify: `publish_bom.py`
- Test: `tests/test_publish_bom.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_publish_bom.py`:

```python
# --------------------------------------------------------------------------- selection

def test_planned_jobs_honors_each_type_flag():
    row = _scanned()   # has both a model and a drawing

    both = publish_bom._planned_jobs(row)
    assert {k for k, _n, _i in both} == {"PDF", "STEP"}

    step_only = publish_bom._planned_jobs(row, include_pdf=False)
    assert {k for k, _n, _i in step_only} == {"STEP"}

    pdf_only = publish_bom._planned_jobs(row, include_step=False)
    assert {k for k, _n, _i in pdf_only} == {"PDF"}

    neither = publish_bom._planned_jobs(row, include_pdf=False,
                                        include_step=False)
    assert neither == []


@pytest.mark.asyncio
async def test_submit_with_pdf_disabled_queues_only_step_jobs():
    api = FakeAPI()
    result = await publish_bom.submit_jobs(api, "1", [_scanned()],
                                           include_pdf=False)

    assert len(api.submitted) == 1
    assert "STEP" in api.submitted[0]["job_type"]
    assert result["submitted"] == 1


@pytest.mark.asyncio
async def test_submit_with_step_disabled_queues_only_pdf_jobs():
    api = FakeAPI()
    result = await publish_bom.submit_jobs(api, "1", [_scanned()],
                                           include_step=False)

    assert len(api.submitted) == 1
    assert "PDF" in api.submitted[0]["job_type"]
    assert result["submitted"] == 1


@pytest.mark.asyncio
async def test_submit_with_both_types_disabled_does_nothing_at_all():
    """Not even the advisory queue check — there is no work to annotate."""
    api = FakeAPI()
    result = await publish_bom.submit_jobs(api, "1", [_scanned()],
                                           include_pdf=False,
                                           include_step=False)

    assert api.submitted == []
    assert result == {"submitted": 0, "failed": 0, "jobs": []}


@pytest.mark.asyncio
async def test_submit_defaults_still_queue_both_kinds():
    """Backward compatibility: every existing caller passes no flags."""
    api = FakeAPI()
    await publish_bom.submit_jobs(api, "1", [_scanned()])

    kinds = {("PDF" if "PDF" in j["job_type"] else "STEP")
             for j in api.submitted}
    assert kinds == {"PDF", "STEP"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_publish_bom.py -k "planned_jobs_honors or pdf_disabled or step_disabled or both_types_disabled" -v`
Expected: FAIL — `TypeError: _planned_jobs() got an unexpected keyword argument 'include_pdf'`

- [ ] **Step 3: Add the flags to `_planned_jobs`**

In `publish_bom.py`, replace the whole `_planned_jobs` function:

```python
def _planned_jobs(
    row: ScanRow,
    *,
    include_pdf: bool = True,
    include_step: bool = True,
) -> list[tuple[str, str, str]]:
    """(kind, file name, file version id) for each job this row implies.

    The single place that decides what a row means. ``count_planned_jobs``
    calls this rather than re-deriving the rule, so a displayed count cannot
    drift from what is actually submitted.

    The flags are the user's output-type choice. They default to permissive so
    that every caller predating the selection feature behaves unchanged.
    """
    jobs: list[tuple[str, str, str]] = []
    if include_pdf and row.drawing_version_id:
        jobs.append(("PDF", row.drawing_name, row.drawing_version_id))
    if include_step and row.model_version_id:
        jobs.append(("STEP", row.model_name, row.model_version_id))
    return jobs
```

- [ ] **Step 4: Add the flags to `submit_jobs`**

In `publish_bom.py`, change the signature of `submit_jobs` from:

```python
async def submit_jobs(
    api,
    vault_id: str,
    scan_rows_in: list[ScanRow],
    on_progress: Optional[ProgressFn] = None,
    *,
    priority: int = 10,
) -> dict[str, Any]:
```

to:

```python
async def submit_jobs(
    api,
    vault_id: str,
    scan_rows_in: list[ScanRow],
    on_progress: Optional[ProgressFn] = None,
    *,
    priority: int = 10,
    include_pdf: bool = True,
    include_step: bool = True,
) -> dict[str, Any]:
```

Immediately after the `progress: ProgressFn = ...` line at the top of the
body, and **before** the `try:` that wraps the queue check, insert:

```python
    # Nothing to do, so do not even run the advisory queue check — a warning
    # about a queue we are not going to use is noise.
    if not (include_pdf or include_step):
        return {"submitted": 0, "failed": 0, "jobs": []}
```

Then change the loop's call from `_planned_jobs(row)` to:

```python
        for kind, name, fvid in _planned_jobs(
            row, include_pdf=include_pdf, include_step=include_step
        ):
```

Finally add these two lines to the `submit_jobs` docstring, just above the
`Returns` line:

```
    ``include_pdf`` / ``include_step`` are the user's output-type choice.
    Both default to True, which is the pre-selection behavior.
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_publish_bom.py -q`
Expected: PASS — 57 passed

- [ ] **Step 6: Commit**

```bash
git add publish_bom.py tests/test_publish_bom.py
git commit -m "feat(publish-bom): filter submitted jobs by output type"
```

---

## Task 2: Engine — `count_planned_jobs`

**Files:**
- Modify: `publish_bom.py`
- Test: `tests/test_publish_bom.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_publish_bom.py`:

```python
def test_count_planned_jobs_breaks_down_by_kind():
    rows = [
        _scanned(),                                          # model + drawing
        _scanned(stem="CD-2", drawing="", drawing_id=""),    # model only
        _scanned(stem="CD-3", model="", model_id="",
                 drawing="", drawing_id=""),                 # nothing
    ]
    assert publish_bom.count_planned_jobs(rows) == {
        "pdf": 1, "step": 2, "total": 3}


def test_count_planned_jobs_honors_the_type_flags():
    rows = [_scanned(), _scanned(stem="CD-2", drawing="", drawing_id="")]

    assert publish_bom.count_planned_jobs(rows, include_pdf=False) == {
        "pdf": 0, "step": 2, "total": 2}
    assert publish_bom.count_planned_jobs(rows, include_step=False) == {
        "pdf": 1, "step": 0, "total": 1}
    assert publish_bom.count_planned_jobs(
        rows, include_pdf=False, include_step=False) == {
        "pdf": 0, "step": 0, "total": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize("include_pdf,include_step", [
    (True, True), (True, False), (False, True), (False, False),
])
async def test_the_predicted_count_matches_what_is_actually_submitted(
    include_pdf, include_step
):
    """The label and the behavior must not be able to disagree.

    A previous defect in this module had summarize() counting one way while
    the code behaved another. This pins the count to reality across every
    flag combination rather than trusting that they were written to match.
    """
    rows = [
        _scanned(),
        _scanned(stem="CD-2", model="CD-2.iam", model_id="7",
                 drawing="", drawing_id=""),
        _scanned(stem="CD-3", model="", model_id="",
                 drawing="CD-3.idw", drawing_id="8"),
    ]
    predicted = publish_bom.count_planned_jobs(
        rows, include_pdf=include_pdf, include_step=include_step)

    api = FakeAPI()
    result = await publish_bom.submit_jobs(
        api, "1", rows, include_pdf=include_pdf, include_step=include_step)

    assert result["submitted"] == predicted["total"]
    assert len(api.submitted) == predicted["total"]
    actual_pdf = sum(1 for j in api.submitted if "PDF" in j["job_type"])
    actual_step = sum(1 for j in api.submitted if "STEP" in j["job_type"])
    assert actual_pdf == predicted["pdf"]
    assert actual_step == predicted["step"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_publish_bom.py -k "count_planned or predicted_count" -v`
Expected: FAIL — `AttributeError: module 'publish_bom' has no attribute 'count_planned_jobs'`

- [ ] **Step 3: Write the implementation**

In `publish_bom.py`, add this immediately after `_planned_jobs`:

```python
def count_planned_jobs(
    rows: list[ScanRow],
    *,
    include_pdf: bool = True,
    include_step: bool = True,
) -> dict[str, int]:
    """How many jobs ``submit_jobs`` would queue for ``rows``.

    Implemented by calling ``_planned_jobs`` rather than re-deriving the rule,
    so the number shown to the user cannot drift from the number submitted.
    """
    counts = {"pdf": 0, "step": 0, "total": 0}
    for row in rows:
        for kind, _name, _fvid in _planned_jobs(
            row, include_pdf=include_pdf, include_step=include_step
        ):
            counts["pdf" if kind == "PDF" else "step"] += 1
            counts["total"] += 1
    return counts
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_publish_bom.py -q`
Expected: PASS — 63 passed

- [ ] **Step 5: Commit**

```bash
git add publish_bom.py tests/test_publish_bom.py
git commit -m "feat(publish-bom): count planned jobs off the same rule that submits them"
```

---

## Task 3: Engine — `merge_selection`

**Files:**
- Modify: `publish_bom.py`
- Test: `tests/test_publish_bom.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_publish_bom.py`:

```python
def test_merge_selection_keeps_a_ticked_part_ticked():
    result = publish_bom.merge_selection(
        previous={"CD-1"}, previous_stems={"CD-1", "CD-2"},
        new_stems={"CD-1", "CD-2"})
    assert result == {"CD-1"}


def test_merge_selection_keeps_an_unticked_part_unticked():
    result = publish_bom.merge_selection(
        previous={"CD-1"}, previous_stems={"CD-1", "CD-2"},
        new_stems={"CD-1", "CD-2"})
    assert "CD-2" not in result


def test_merge_selection_ticks_a_part_that_is_new_this_scan():
    """A part that just appeared must not be silently excluded."""
    result = publish_bom.merge_selection(
        previous={"CD-1"}, previous_stems={"CD-1", "CD-2"},
        new_stems={"CD-1", "CD-2", "CD-3"})
    assert "CD-3" in result


def test_merge_selection_drops_a_part_that_is_gone():
    result = publish_bom.merge_selection(
        previous={"CD-1", "CD-2"}, previous_stems={"CD-1", "CD-2"},
        new_stems={"CD-1"})
    assert result == {"CD-1"}


def test_merge_selection_ticks_everything_on_a_first_scan():
    """No previous scan means no prior intent to respect."""
    result = publish_bom.merge_selection(
        previous=set(), previous_stems=set(),
        new_stems={"CD-1", "CD-2"})
    assert result == {"CD-1", "CD-2"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_publish_bom.py -k merge_selection -v`
Expected: FAIL — `AttributeError: module 'publish_bom' has no attribute 'merge_selection'`

- [ ] **Step 3: Write the implementation**

In `publish_bom.py`, add this immediately after `count_planned_jobs`:

```python
def merge_selection(
    previous: set[str],
    previous_stems: set[str],
    new_stems: set[str],
) -> set[str]:
    """Carry a user's part selection across a re-scan.

    A stem the user already saw keeps whatever state they left it in. A stem
    that is new this scan arrives selected, because a part that just appeared
    should not be silently excluded from the run. A stem that has gone is
    dropped.

    Pure set logic over stems, kept here rather than in a widget callback so
    it can be tested without Tk. A first scan has no prior intent to respect,
    so everything comes back selected.
    """
    return {s for s in new_stems if s not in previous_stems or s in previous}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_publish_bom.py -q`
Expected: PASS — 68 passed

- [ ] **Step 5: Commit**

```bash
git add publish_bom.py tests/test_publish_bom.py
git commit -m "feat(publish-bom): preserve part selection across a re-scan"
```

---

## Task 4: Dialog — checkbox column and toggling

**Files:**
- Modify: `gui/publish_bom.py`

No unit tests — the GUI is untested by repo convention. Task 6 drives it
headlessly.

- [ ] **Step 1: Add the state and glyph constants**

In `gui/publish_bom.py`, immediately after the `import publish_bom` block, add:

```python
# ASCII rather than Unicode ballot boxes: those render inconsistently across
# Windows fonts, and this table already uses ASCII hyphens for that reason.
CHECKED = "[x]"
UNCHECKED = "[ ]"
```

In `__init__`, immediately after the `self.summary_text = ...` line, add:

```python
        self.queue_text = tk.StringVar(value="")
        self.want_pdf = tk.BooleanVar(value=True)
        self.want_step = tk.BooleanVar(value=True)
        # Part stems the user wants published. Stems rather than row indices:
        # that is what lets a selection survive a re-scan.
        self.selected: set[str] = set()
        self._prev_stems: set[str] = set()
```

- [ ] **Step 2: Add the checkbox column to the Treeview**

In `_build_ui`, replace:

```python
        columns = ("part", "description", "model", "drawing", "status")
```

with:

```python
        columns = ("sel", "part", "description", "model", "drawing", "status")
```

and replace the column-configuration tuple:

```python
        for key, label, width in (
            ("part", "Part", 130),
            ("description", "Description", 200),
            ("model", "Model", 170),
            ("drawing", "Drawing", 170),
            ("status", "Status", 170),
        ):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="w")
```

with:

```python
        for key, label, width, anchor in (
            ("sel", "", 34, "center"),
            ("part", "Part", 120, "w"),
            ("description", "Description", 190, "w"),
            ("model", "Model", 160, "w"),
            ("drawing", "Drawing", 160, "w"),
            ("status", "Status", 165, "w"),
        ):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor=anchor, stretch=False)
```

Immediately after the `self.tree.tag_configure("gap", ...)` line, add the
bindings:

```python
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<space>", self._on_space)
```

- [ ] **Step 3: Add the toggle handlers**

In `gui/publish_bom.py`, add these methods immediately after `_invalidate_scan`:

```python
    # ----- Selection --------------------------------------------------------

    def _on_tree_click(self, event) -> None:
        """Toggle when the checkbox cell itself is clicked.

        Scoped to column #1 so clicking any other cell still selects the row
        normally for reading.
        """
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != "#1":
            return
        iid = self.tree.identify_row(event.y)
        if iid:
            self._toggle([iid])

    def _on_space(self, _event) -> str:
        """Toggle every selected row.

        Treeview already supports shift-click and ctrl-click ranges, so this
        turns 'tick these fifteen' into drag-then-space.
        """
        rows = self.tree.selection()
        if rows:
            self._toggle(rows)
        return "break"

    def _toggle(self, stems) -> None:
        for stem in stems:
            if stem in self.selected:
                self.selected.discard(stem)
            else:
                self.selected.add(stem)
        self._refresh_checks()

    def _refresh_checks(self) -> None:
        """Repaint the glyphs and recompute what Submit would queue."""
        for iid in self.tree.get_children():
            self.tree.set(
                iid, "sel", CHECKED if iid in self.selected else UNCHECKED)
        self._update_queue_line()

    def _update_queue_line(self) -> None:
        rows = [r for r in self.scan_result if r.stem in self.selected]
        counts = publish_bom.count_planned_jobs(
            rows,
            include_pdf=self.want_pdf.get(),
            include_step=self.want_step.get(),
        )
        if self.scan_result:
            self.queue_text.set(
                f"Queueing {counts['total']} job(s): {counts['pdf']} PDF + "
                f"{counts['step']} STEP  "
                f"({len(rows)} of {len(self.scan_result)} parts)"
            )
        else:
            self.queue_text.set("")
        self.submit_btn.configure(
            state="normal" if counts["total"] and not self._busy else "disabled"
        )
```

The Treeview row `iid` is the part stem (set in Step 6), so the iterables above
are stems throughout — there is no separate lookup table to keep in sync.

- [ ] **Step 4: Commit**

```bash
git add gui/publish_bom.py
git commit -m "feat(publish-bom): checkbox column with click and spacebar toggling"
```

---

## Task 5: Dialog — type toggles, bulk buttons, queue line

**Files:**
- Modify: `gui/publish_bom.py`

- [ ] **Step 1: Add the type toggles to the actions row**

In `_build_ui`, find the `actions` frame where `self.scan_btn` is packed.
Immediately after `actions.pack(fill="x")` and **before** `self.scan_btn` is
created, insert:

```python
        tk.Label(actions, text="Generate:", bg=LIGHT_GRAY, fg=DARK_BLUE,
                 font=("Arial", 9, "bold")).pack(side="left", padx=(0, 6))
        for text, var in (("PDF drawings", self.want_pdf),
                          ("STEP files", self.want_step)):
            tk.Checkbutton(
                actions, text=text, variable=var, bg=LIGHT_GRAY,
                fg=DARK_GRAY, font=("Arial", 9), activebackground=LIGHT_GRAY,
                selectcolor=WHITE, command=self._update_queue_line,
            ).pack(side="left", padx=(0, 10))
```

- [ ] **Step 2: Add the bulk-select strip**

In `_build_ui`, immediately after the `actions` frame block (after
`self.submit_btn.pack(...)`), insert:

```python
        bulk = tk.Frame(self.win, bg=LIGHT_GRAY, padx=16)
        bulk.pack(fill="x", pady=(6, 0))
        tk.Label(bulk, text="Select:", bg=LIGHT_GRAY, fg=DARK_BLUE,
                 font=("Arial", 9, "bold")).pack(side="left", padx=(0, 6))
        for text, command in (
            ("All", self._select_all),
            ("None", self._select_none),
            ("Invert", self._select_invert),
            ("Missing drawing", self._select_missing_drawing),
            ("Both files", self._select_both_files),
        ):
            tk.Button(bulk, text=text, command=command, font=("Arial", 8),
                      relief="groove", padx=6, pady=1,
                      cursor="hand2").pack(side="left", padx=(0, 4))
```

- [ ] **Step 3: Add the queue line under the summary line**

In `_build_ui`, immediately after the `tk.Label(self.win, textvariable=self.summary_text, ...)` block, insert:

```python
        tk.Label(self.win, textvariable=self.queue_text, bg=PALE_BLUE,
                 fg=OLIVE_GREEN, font=("Arial", 9, "bold"), anchor="w",
                 padx=16).pack(fill="x", pady=(0, 4))
```

- [ ] **Step 4: Add the bulk-select methods**

Add these immediately after `_update_queue_line`:

```python
    def _set_selection(self, stems: set[str]) -> None:
        self.selected = stems
        self._refresh_checks()

    def _select_all(self) -> None:
        self._set_selection({r.stem for r in self.scan_result})

    def _select_none(self) -> None:
        self._set_selection(set())

    def _select_invert(self) -> None:
        self._set_selection({r.stem for r in self.scan_result
                             if r.stem not in self.selected})

    def _select_missing_drawing(self) -> None:
        """Exactly the parts that need a drawing made — the gap report.

        Prefix match: an ambiguous stem carries a '(multiple matches)' suffix,
        and comparing the whole string would skip those rows silently.
        """
        self._set_selection({
            r.stem for r in self.scan_result
            if r.status.startswith(publish_bom.STATUS_MODEL_ONLY)
        })

    def _select_both_files(self) -> None:
        """Exactly the parts that can produce a PDF and a STEP."""
        self._set_selection({
            r.stem for r in self.scan_result
            if r.model_version_id and r.drawing_version_id
        })
```

These replace the selection rather than adding to it: they answer a question,
and a filter that quietly unioned with whatever was already ticked would not
answer it.

- [ ] **Step 5: Commit**

```bash
git add gui/publish_bom.py
git commit -m "feat(publish-bom): output-type toggles and bulk select controls"
```

---

## Task 6: Dialog — wire selection into render and submit

**Files:**
- Modify: `gui/publish_bom.py`

- [ ] **Step 1: Rewrite `_render_scan`**

Replace the whole `_render_scan` method with:

```python
    def _render_scan(self, rows: list[publish_bom.ScanRow]) -> None:
        new_stems = {r.stem for r in rows}
        self.selected = publish_bom.merge_selection(
            self.selected, self._prev_stems, new_stems)
        self._prev_stems = new_stems
        self.scan_result = rows

        for row in rows:
            part = f"{row.stem} (top)" if row.is_top else row.stem
            tags = () if row.status == publish_bom.STATUS_BOTH else ("gap",)
            # The stem is the row id: it is already unique per scan, so there
            # is no separate iid-to-stem table to keep in sync.
            self.tree.insert("", "end", iid=row.stem, values=(
                UNCHECKED, part, row.description or "-",
                row.model_name or "-", row.drawing_name or "-", row.status,
            ), tags=tags)

        s = publish_bom.summarize(rows)
        parts = [
            f"{s['rows']} part(s)",
            f"{s['models']} model(s)",
            f"{s['drawings']} drawing(s)",
            f"{s['jobs']} job(s)",
            f"{s['missing_drawing']} missing a drawing",
            f"{s['not_found']} not in Vault",
        ]
        for count, label in ((s["failed"], "lookup failed"),
                             (s["truncated"], "search truncated"),
                             (s["ambiguous"], "ambiguous")):
            if count:
                parts.append(f"{count} {label}")
        self.summary_text.set(" - ".join(parts))

        self._log(f"Scan complete: {s['jobs']} job(s) available to queue.", "ok")
        # Clear busy before repainting: _update_queue_line consults _busy when
        # deciding whether Submit may be enabled.
        self._set_busy(False)
        self._refresh_checks()
```

Note the summary line now says "job(s)" where it previously said
"job(s) to queue" — how many will actually be queued is the queue line's job
now, and having both claim it invites reading one as the other.

- [ ] **Step 2: Clear selection state in `_invalidate_scan`**

In `_invalidate_scan`, immediately after the `self.scan_result = []` line, add:

```python
        self.selected = set()
        self._prev_stems = set()
        self.queue_text.set("")
```

- [ ] **Step 3: Pass the selection to submit**

In `_on_submit`, replace:

```python
        s = publish_bom.summarize(self.scan_result)
        if not messagebox.askyesno(
            "Queue jobs?",
            f"Queue {s['jobs']} job(s) on the Vault job server?\n\n"
            "Jobs are submitted and not tracked from here — watch their "
            "progress in Vault Explorer.",
            parent=self.win,
        ):
            return
```

with:

```python
        rows = [r for r in self.scan_result if r.stem in self.selected]
        counts = publish_bom.count_planned_jobs(
            rows,
            include_pdf=self.want_pdf.get(),
            include_step=self.want_step.get(),
        )
        if not counts["total"]:
            return
        if not messagebox.askyesno(
            "Queue jobs?",
            f"Queue {counts['total']} job(s) "
            f"({counts['pdf']} PDF, {counts['step']} STEP) for "
            f"{len(rows)} part(s) on the Vault job server?\n\n"
            "Jobs are submitted and not tracked from here — watch their "
            "progress in Vault Explorer.",
            parent=self.win,
        ):
            return
```

Then, further down in `_on_submit`, replace:

```python
        self._log(f"Submitting {s['jobs']} job(s)...")

        rows = list(self.scan_result)
```

with:

```python
        self._log(f"Submitting {counts['total']} job(s)...")
```

(`rows` is now bound above, so the old rebinding must go.)

Finally, in the `runner()` closure inside `_on_submit`, change the
`submit_jobs` call to pass the flags:

```python
                result = asyncio.run(publish_bom.submit_jobs(
                    self.api, self.vault_id, rows,
                    on_progress=lambda m: self.q.put(("log", m)),
                    include_pdf=self.want_pdf.get(),
                    include_step=self.want_step.get(),
                ))
```

- [ ] **Step 4: Keep Submit disabled after submitting**

In `_render_submit`, the last line currently forces Submit disabled. Leave it
exactly as it is — a second run still needs a fresh Scan.

- [ ] **Step 5: Verify it imports and the suite is green**

Run: `python -c "import gui.publish_bom; print('ok')"`
Expected: `ok`

Run: `python -m pytest -q`
Expected: 392 passed, 1-2 skipped

- [ ] **Step 6: Commit**

```bash
git add gui/publish_bom.py
git commit -m "feat(publish-bom): submit only the selected parts and types"
```

---

## Task 7: Headless verification of the dialog

**Files:** none committed — verification only.

- [ ] **Step 1: Write a throwaway drive script**

Write to the scratchpad (NOT into the repo), then run it. It must construct
the dialog against a hidden root and assert real behavior:

```python
import os, sys, tkinter as tk
ROOT = r"C:\Users\ZakOlech\Documents\Custom Programs\Vault-MCP"
sys.path.insert(0, ROOT); os.chdir(ROOT)
import publish_bom
from gui.publish_bom import PublishBOMGUI, CHECKED, UNCHECKED

def row(stem, model, drawing, is_top=False):
    r = publish_bom.ScanRow(stem=stem, description=f"desc {stem}", is_top=is_top,
                            model_name=model, model_version_id="1" if model else "",
                            drawing_name=drawing,
                            drawing_version_id="2" if drawing else "")
    r.status = publish_bom._status_for(r)
    return r

ROWS = [
    row("CD-1", "CD-1.ipt", "CD-1.idw"),      # both
    row("CD-2", "CD-2.iam", ""),              # model only
    row("CD-3", "", ""),                      # nothing
    row("CD-4", "CD-4.ipt", "CD-4.idw"),      # both
]

root = tk.Tk(); root.withdraw()
g = PublishBOMGUI(root, api=None, vault_id="", cfg={})
g.bom_path.set(os.path.join(ROOT, "tests", "fixtures", "CD-001608-bom.xlsx"))
g._render_scan(ROWS)

# everything ticked by default, both types on
assert g.selected == {"CD-1", "CD-2", "CD-3", "CD-4"}, g.selected
print("default selection :", g.queue_text.get())
assert "3 PDF" not in g.queue_text.get()   # 2 PDF + 3 STEP = 5
assert "Queueing 5 job(s): 2 PDF + 3 STEP" in g.queue_text.get()
assert str(g.submit_btn["state"]) == "normal"

# glyphs painted
assert g.tree.set("CD-1", "sel") == CHECKED

# toggle one row off
g._toggle(["CD-1"])
assert g.tree.set("CD-1", "sel") == UNCHECKED
print("after untick CD-1 :", g.queue_text.get())
assert "Queueing 3 job(s): 1 PDF + 2 STEP  (3 of 4 parts)" in g.queue_text.get()

# type toggle off: removes PDF jobs but must NOT untick any row
before = set(g.selected)
g.want_pdf.set(False); g._update_queue_line()
print("PDF off           :", g.queue_text.get())
assert "0 PDF" in g.queue_text.get()
assert g.selected == before, "a type toggle must not change row selection"
assert g.tree.set("CD-4", "sel") == CHECKED, "row glyphs must be untouched"
g.want_pdf.set(True); g._update_queue_line()

# bulk buttons
g._select_none()
assert g.selected == set()
assert str(g.submit_btn["state"]) == "disabled", "no jobs -> Submit must be off"
g._select_all(); assert len(g.selected) == 4
g._select_invert(); assert g.selected == set()
g._select_missing_drawing(); assert g.selected == {"CD-2"}, g.selected
g._select_both_files(); assert g.selected == {"CD-1", "CD-4"}, g.selected
print("bulk buttons      : ok")

# both types off -> nothing queued, Submit off
g.want_pdf.set(False); g.want_step.set(False); g._update_queue_line()
assert str(g.submit_btn["state"]) == "disabled"
g.want_pdf.set(True); g.want_step.set(True); g._update_queue_line()

# re-scan preserves selection; a new part arrives ticked
g._select_none(); g._toggle(["CD-2"])
for iid in g.tree.get_children():
    g.tree.delete(iid)
g._render_scan(ROWS + [row("CD-5", "CD-5.ipt", "")])
assert g.selected == {"CD-2", "CD-5"}, g.selected
print("re-scan merge     : kept CD-2, added CD-5")

# retargeting the BOM clears everything
g.bom_path.set(r"C:\elsewhere\CD-999 BOM.xlsx")
assert g.selected == set() and g.queue_text.get() == ""
assert str(g.submit_btn["state"]) == "disabled"
print("retarget clears   : ok")

root.destroy()
print("\nALL SELECTION ASSERTIONS PASSED")
```

- [ ] **Step 2: Run it**

Run the script. Expected: every line prints and `ALL SELECTION ASSERTIONS PASSED`.
If Tk cannot start for lack of a display, report that explicitly rather than
skipping silently.

- [ ] **Step 3: Fix anything it catches, then re-run**

If an assertion fails, fix the dialog (not the assertion, unless the assertion
is provably wrong), and re-run until clean. Commit any fix:

```bash
git add gui/publish_bom.py
git commit -m "fix(publish-bom): <what the headless drive caught>"
```

Skip the commit if nothing needed fixing.

---

## Task 8: Live re-verification

**Files:** none — verification only.

- [ ] **Step 1: Confirm the scan is unchanged against the real vault**

The scan path must be untouched by this change. Run the engine against the
real BOM and confirm the same numbers as before:

```bash
python - <<'PY'
import json, asyncio, sys
sys.path.insert(0, '.')
from vault_rest_api import VaultRestAPI
import publish_bom
cfg = json.load(open('config.json'))['vault']
BOM = r"C:\Vault Workspace\DESIGNS\PRODUCTION EQUIPMENT\CD-001608 BOM.xlsx"

async def main():
    api = VaultRestAPI(cfg['servername'])
    r = await api.create_session(database=cfg['database'],
                                 username=cfg['username'],
                                 password=cfg['password'])
    d = r['data']; vid = str((d.get('vaultInformation') or {}).get('id','') or '1')
    rows, err = await publish_bom.scan_bom(
        api, vid, BOM, top_assembly=publish_bom.top_assembly_stem(BOM))
    if err:
        print("ERROR:", err); return
    print("summarize:", json.dumps(publish_bom.summarize(rows)))
    for flags in ({}, {"include_pdf": False}, {"include_step": False}):
        print(flags or "both", "->",
              publish_bom.count_planned_jobs(rows, **flags))

asyncio.run(main())
PY
```

Expected: `summarize` reports `rows=10, models=10, drawings=3, jobs=13,
missing_drawing=7, not_found=0`. Counts: both → `total 13`;
`include_pdf=False` → `total 10` (STEP only); `include_step=False` → `total 3`
(PDF only).

**Do not submit any jobs in this step.** Submission against the live vault is
the user's call.

- [ ] **Step 2: Report the numbers**

Report the actual output. If the scan numbers differ from the expected values
above, stop and say so — the scan path was supposed to be untouched, so a
difference means something regressed.

---

## Notes for the implementer

**Do not put selection state on `ScanRow`.** It is a description of what is in
Vault, not of what the user clicked. The GUI owns the selection and hands
`submit_jobs` a filtered list.

**Do not re-derive the job rule anywhere.** `_planned_jobs` is the only place
that decides what a row implies; `count_planned_jobs` calls it. A count
computed independently is how the earlier `summarize` defect happened.

**Match status strings by prefix.** The `(multiple matches)` suffix makes
equality comparisons silently wrong.

**Do not change the scan, the job params, or the fire-and-forget model.**
