"""BOM → Wrike manufacturing tasks.

Reads a generated purchasing workbook, reconciles each part's supplier against
the Vault Vendor property, groups the parts into one order per supplier, and
creates a Wrike parent task with dependency-chained Purchasing /
Manufacturing / Shipping subtasks.

One trio per supplier, never one per part: a supplier's order is one PO and
one shipment, so eleven screws from McMaster are one set of tasks with eleven
line items. A Buy-only order has no Manufacturing task — nothing is made.

This module is the engine. The GUI wrapper lives in ``gui/wrike_mfg_tasks.py``.
See ``docs/superpowers/specs/2026-07-29-wrike-mfg-tasks-design.md``.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Optional

import bom_purchasing

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str], None]

KIND_MAKE = "Make"
KIND_BUY = "Buy"


@dataclass
class OrderPart:
    """One line item on one supplier's order.

    ``title`` is the identity — on the Purchasing tab it is headed "Name" and
    carries the CAD number, which is also the Vault file stem the supplier
    lookup searches for. The sheet hides the Number column, so there is no
    part number to carry alongside it.
    """
    title: str
    description: str = ""
    kind: str = KIND_MAKE
    qty: float = 1.0
    material: str = ""
    revision: str = ""
    unit_cost: float = 0.0
    shipping: float = 0.0
    tax: float = 0.0
    lead_time_days: Optional[int] = None
    sheet_vendor: str = ""

    @property
    def line_total(self) -> float:
        """Recomputed, never read from the sheet: Sub Total is a formula and
        openpyxl returns None for it unless Excel has re-saved the file."""
        return self.unit_cost * self.qty + self.shipping + self.tax


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if value != value:            # NaN
            return ""
    except Exception:                 # noqa: BLE001
        pass
    return str(value).strip()


def _to_float(value: Any, default: float = 0.0) -> float:
    text = _text(value).replace(",", "").replace("$", "")
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def _to_int_or_none(value: Any) -> Optional[int]:
    text = _text(value).replace(",", "")
    if not text:
        return None
    try:
        return int(round(float(text)))
    except ValueError:
        return None


def load_order_parts(
    sheet_path: str,
    on_progress: Optional[ProgressFn] = None,
) -> tuple[list[OrderPart], str, Optional[str]]:
    """Parse a generated purchasing workbook into orderable line items.

    Returns ``(parts, assembly_number, error)``. ``error`` is None on success;
    on failure it is a message meant to be shown verbatim and ``parts`` is
    empty.

    Roll-up rows are excluded: an assembly's Sub Total is a SUM of its
    children, so ordering both double-counts the cost and puts the same metal
    on the PO twice. Duplicate titles collapse to one line item.
    """
    progress: ProgressFn = on_progress or (lambda _msg: None)

    df, assembly, error = bom_purchasing.read_purchasing_sheet(sheet_path)
    if error:
        return [], "", error

    children_map = bom_purchasing.build_children_map(df)

    merged: dict[str, OrderPart] = {}
    order: list[str] = []

    for idx, rec in df.iterrows():
        if children_map.get(idx):
            title = _text(rec.get("Title")) or "(unnamed)"
            logger.info("Excluding roll-up row %s", title)
            progress(f"  {title}: sub-assembly roll-up, ordering its children")
            continue

        title = _text(rec.get("Title"))
        if not title:
            label = _text(rec.get("Row Order")) or "(unnumbered)"
            logger.info("Skipping row %s: no name", label)
            progress(f"  Row {label} has no name; skipped.")
            continue

        source = _text(rec.get("Source")).lower()
        kind = KIND_MAKE if source == "make" else KIND_BUY

        raw_qty = rec.get("Item Qty")
        qty = _to_float(raw_qty, default=0.0)
        if qty <= 0:
            # The sheet's own roll-up formula guards Qty with ISNUMBER for the
            # same reason: the root row often carries "-".
            if _text(raw_qty):
                progress(f"  {title}: quantity {_text(raw_qty)!r} is not a "
                         f"number; counting 1.")
            qty = 1.0

        lead = _to_int_or_none(rec.get("Lead Time (Business Days)"))

        key = title.casefold()
        existing = merged.get(key)
        if existing is None:
            merged[key] = OrderPart(
                title=title,
                description=_text(rec.get("Description (Item,CO)")),
                kind=kind,
                qty=qty,
                material=_text(rec.get("Material")),
                revision=_text(rec.get("Revision")),
                unit_cost=_to_float(rec.get("Cost Per")),
                shipping=_to_float(rec.get("Shipping")),
                tax=_to_float(rec.get("Tax/Tariff")),
                lead_time_days=lead,
                sheet_vendor=_text(rec.get("Vendor")),
            )
            order.append(key)
        else:
            existing.qty += qty
            if lead is not None:
                existing.lead_time_days = max(existing.lead_time_days or 0, lead)
            progress(f"  {title}: appears more than once; quantities summed "
                     f"to {existing.qty:g}.")

    return [merged[k] for k in order], assembly, None


# ---------------------------------------------------------------------------
# Stage 2: reconcile each part's supplier against Vault
# ---------------------------------------------------------------------------

# Vault caps concurrent work anyway; this keeps a 200-row sheet from opening
# 200 sockets at once. Same cap publish_bom.py uses.
MAX_CONCURRENCY = 8

# A title's keyword search also matches its .pdf/.stp siblings, its item, and
# anything carrying the title in a property, so the hit list is much longer
# than the one file wanted.
SEARCH_LIMIT = 50

VENDOR_PROPERTY = "Vendor"
MODEL_EXTS = ("ipt", "iam")

STATUS_MATCHED = "matched"
STATUS_SHEET_ONLY = "sheet only"
STATUS_VAULT_ONLY = "Vault only"
STATUS_MISMATCH = "mismatch"
STATUS_BOTH_BLANK = "both blank"
STATUS_NOT_IN_VAULT = "not in Vault"
STATUS_LOOKUP_FAILED = "lookup failed"
STATUS_TRUNCATED = "search truncated"


@dataclass
class ReconcileRow:
    """An OrderPart plus what Vault says about its supplier.

    ``proposal`` is the value the GUI offers with one click; empty means the
    tool has nothing defensible to suggest and a human must decide.
    ``chosen`` is what will actually be used.
    """
    part: OrderPart
    vault_vendor: str = ""
    status: str = ""
    proposal: str = ""
    chosen: str = ""
    excluded: bool = False

    @property
    def resolved(self) -> bool:
        return self.excluded or bool(self.chosen)


_PUNCTUATION = re.compile(r"[^0-9A-Za-z]+")


def vendor_key(value: str) -> str:
    """Normalized form for comparing and grouping supplier names.

    Case, surrounding whitespace, runs of internal whitespace, and punctuation
    all collapse: the reference BOM spells it McMASTER-CARR, a supplier typed
    as "machine  shop" is the same vendor as "Machine Shop", and a real sheet
    spelled the same shop "In House" on one row and "In-house" on another —
    without punctuation folding those produced two separate one-part orders
    for a single shop.
    """
    return _PUNCTUATION.sub(" ", _text(value)).strip().casefold()


def _same_vendor(left: str, right: str) -> bool:
    return vendor_key(left) == vendor_key(right)


def _search_results(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("results", "items", "data", "value"):
            inner = data.get(key)
            if isinstance(inner, list):
                return [r for r in inner if isinstance(r, dict)]
    return []


def _vendor_prop_ids(data: Any) -> set[str]:
    """Property-definition ids whose display name is Vendor.

    The response carries properties as {propertyDefinitionId, value} plus an
    included.propertyDefinition map that names them, so no separate
    /property-definitions call is needed.
    """
    ids: set[str] = set()
    if not isinstance(data, dict):
        return ids
    included = data.get("included")
    defs = included.get("propertyDefinition") if isinstance(included, dict) else None
    if not isinstance(defs, dict):
        return ids
    for pid, meta in defs.items():
        name = ""
        if isinstance(meta, dict):
            name = _text(meta.get("displayName") or meta.get("name"))
        if name.casefold() == VENDOR_PROPERTY.casefold():
            ids.add(pid)
    return ids


def _vendor_of(record: dict[str, Any], vendor_ids: set[str]) -> str:
    props = record.get("properties")
    if isinstance(props, dict):
        for key, value in props.items():
            if _text(key).casefold() == VENDOR_PROPERTY.casefold():
                return _text(value)
        return ""
    if isinstance(props, list):
        for prop in props:
            if not isinstance(prop, dict):
                continue
            pid = _text(prop.get("propertyDefinitionId"))
            name = _text(prop.get("displayName") or prop.get("name"))
            if pid in vendor_ids or name.casefold() == VENDOR_PROPERTY.casefold():
                return _text(prop.get("value"))
    return ""


def _base_stem(name: str) -> str:
    base = os.path.basename(_text(name))
    return base.rsplit(".", 1)[0] if "." in base else base


def _classify(part: OrderPart, vault_vendor: str, *,
              found: bool, failed: bool, truncated: bool) -> tuple[str, str]:
    sheet = part.sheet_vendor

    if truncated:
        return STATUS_TRUNCATED, sheet
    if failed:
        # A transient search error is not evidence about the part.
        return STATUS_LOOKUP_FAILED, sheet
    if not found:
        # A catalogue screw that was never checked in is routine, and the
        # sheet's vendor for a bought part came from the Engineering Purchased
        # Parts list. A missing CD-numbered Make part is not routine.
        return STATUS_NOT_IN_VAULT, sheet if part.kind == KIND_BUY else ""
    if sheet and vault_vendor:
        if _same_vendor(sheet, vault_vendor):
            return STATUS_MATCHED, sheet
        return STATUS_MISMATCH, ""
    if sheet:
        return STATUS_SHEET_ONLY, sheet
    if vault_vendor:
        return STATUS_VAULT_ONLY, vault_vendor
    return STATUS_BOTH_BLANK, ""


async def _reconcile_one(api, vault_id: str, part: OrderPart,
                         progress: ProgressFn) -> ReconcileRow:
    row = ReconcileRow(part=part)

    resp = await api.search_file_versions(
        vault_id=vault_id, query=part.title,
        prop_def_ids="all", latest_only=True, limit=SEARCH_LIMIT,
    )
    if resp.get("error"):
        row.status, row.proposal = _classify(
            part, "", found=False, failed=True, truncated=False)
        progress(f"  {part.title}: lookup failed")
        return row

    hits = _search_results(resp.get("data"))
    vendor_ids = _vendor_prop_ids(resp.get("data"))

    found = False
    for rec in hits:
        # Strictly the version entity, and the basename must EQUAL the title —
        # a substring match pulls in every assembly that references the part.
        if _text(rec.get("entityType")).casefold() != "fileversion":
            continue
        name = _text(rec.get("name"))
        if _base_stem(name).casefold() != part.title.casefold():
            continue
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in MODEL_EXTS:
            continue
        found = True
        vendor = _vendor_of(rec, vendor_ids)
        if vendor:
            row.vault_vendor = vendor
            break

    truncated = not found and len(hits) >= SEARCH_LIMIT
    row.status, row.proposal = _classify(
        part, row.vault_vendor, found=found, failed=False, truncated=truncated)
    if row.status == STATUS_MATCHED:
        row.chosen = row.proposal
    progress(f"  {part.title}: {row.status}")
    return row


async def reconcile_vendors(
    api,
    vault_id: str,
    parts: list[OrderPart],
    on_progress: Optional[ProgressFn] = None,
) -> list[ReconcileRow]:
    """Resolve every part's supplier against the Vault Vendor property.

    Runs at most MAX_CONCURRENCY lookups at once. Output order matches input
    order so the GUI table is stable. A failure on one part degrades that row
    only; it never aborts the reconcile.
    """
    progress: ProgressFn = on_progress or (lambda _msg: None)
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def guarded(part: OrderPart) -> ReconcileRow:
        async with sem:
            try:
                return await _reconcile_one(api, vault_id, part, progress)
            except Exception as exc:  # noqa: BLE001 — one bad row must not sink it
                logger.exception("Reconcile failed for %s", part.title)
                progress(f"  {part.title}: lookup failed - {exc}")
                status, proposal = _classify(
                    part, "", found=False, failed=True, truncated=False)
                return ReconcileRow(part=part, status=status, proposal=proposal)

    return list(await asyncio.gather(*(guarded(p) for p in parts)))


def accept_proposals(rows: list[ReconcileRow]) -> int:
    """Take every row's proposal as its chosen supplier. Returns how many.

    Rows with no proposal — a genuine disagreement, or nothing on either side
    — are left alone. Eleven screws must not mean eleven clicks, but a real
    conflict is never resolved on the user's behalf.
    """
    accepted = 0
    for row in rows:
        if not row.chosen and not row.excluded and row.proposal:
            row.chosen = row.proposal
            accepted += 1
    return accepted


def unresolved_count(rows: list[ReconcileRow]) -> int:
    return sum(1 for r in rows if not r.resolved)


# ---------------------------------------------------------------------------
# Stage 3: group into one order per supplier
# ---------------------------------------------------------------------------

STAGE_PURCHASING = "Purchasing"
STAGE_MANUFACTURING = "Manufacturing"
STAGE_SHIPPING = "Shipping"

# Supplier names that mean "our own shop" rather than a vendor. Compared
# after vendor_key normalization, so punctuation and case do not matter.
IN_HOUSE_KEYS = frozenset({"in house", "inhouse"})


@dataclass
class StageSchedule:
    stage: str
    start: date
    due: date


@dataclass
class SupplierOrder:
    """One supplier's order — one PO, one shipment, one set of tasks."""
    supplier: str
    parts: list[OrderPart] = field(default_factory=list)
    schedule: list[StageSchedule] = field(default_factory=list)

    @property
    def make_parts(self) -> list[OrderPart]:
        return [p for p in self.parts if p.kind == KIND_MAKE]

    @property
    def has_make(self) -> bool:
        return bool(self.make_parts)

    @property
    def is_in_house(self) -> bool:
        """Whether this order is our own shop rather than a vendor.

        There is no PO to issue to yourself, so an in-house order has no
        Purchasing stage.
        """
        return vendor_key(self.supplier) in IN_HOUSE_KEYS

    @property
    def stages(self) -> list[str]:
        """The stages this order passes through. A Buy-only order skips
        Manufacturing — nothing is made, the supplier ships from stock. An
        in-house order skips Purchasing — there is no PO to issue to your own
        shop."""
        if self.is_in_house:
            # No PO to issue to your own shop. Shipping still means moving
            # the finished part to where it is needed.
            return ([STAGE_MANUFACTURING] if self.has_make else []) + [STAGE_SHIPPING]
        if self.has_make:
            return [STAGE_PURCHASING, STAGE_MANUFACTURING, STAGE_SHIPPING]
        return [STAGE_PURCHASING, STAGE_SHIPPING]

    @property
    def piece_count(self) -> float:
        return sum(p.qty for p in self.parts)

    @property
    def total(self) -> float:
        return sum(p.line_total for p in self.parts)

    @property
    def start(self) -> Optional[date]:
        return min((s.start for s in self.schedule), default=None)

    @property
    def due(self) -> Optional[date]:
        return max((s.due for s in self.schedule), default=None)


def group_orders(rows: list[ReconcileRow]) -> list[SupplierOrder]:
    """One order per supplier, in first-seen order.

    Grouping normalizes the supplier name the same way the reconcile
    comparison does, so "xometry" and "Xometry" are one order. The display
    name is the first row's spelling — that is what reaches the task title.
    Excluded and unresolved rows contribute to nothing.
    """
    orders: dict[str, SupplierOrder] = {}
    order: list[str] = []

    for row in rows:
        if row.excluded or not row.chosen:
            continue
        key = vendor_key(row.chosen)
        if key not in orders:
            orders[key] = SupplierOrder(supplier=row.chosen)
            order.append(key)
        orders[key].parts.append(row.part)

    return [orders[k] for k in order]


# ---------------------------------------------------------------------------
# Stage 4: schedule each order forward from a start date
# ---------------------------------------------------------------------------

@dataclass
class Durations:
    """Stage lengths in business days, editable in the GUI.

    ``manufacturing`` is the fallback used when no part in the order carries a
    lead time; ``shipping`` is likewise the fallback for a Buy-only order.
    """
    purchasing: int = 2
    manufacturing: int = 10
    shipping: int = 3


def add_business_days(start: date, days: int) -> date:
    """``days`` business days after ``start``, weekends skipped.

    A start that lands on a weekend snaps forward to the next business day, so
    a Saturday start date never produces a Saturday task. No holiday calendar.
    """
    day = start
    while day.weekday() >= 5:
        day += timedelta(days=1)
    remaining = max(0, days)
    while remaining > 0:
        day += timedelta(days=1)
        if day.weekday() < 5:
            remaining -= 1
    return day


def _stage_length(order: SupplierOrder, stage: str,
                  durations: Durations) -> int:
    """How many business days a stage runs, in the order's own terms.

    Lead time lands on the stage that consumes it: manufacturing for an order
    with Make parts, shipping for one without.
    """
    if stage == STAGE_PURCHASING:
        return max(1, durations.purchasing)
    if stage == STAGE_MANUFACTURING:
        leads = [p.lead_time_days for p in order.make_parts
                 if p.lead_time_days]
        return max(1, max(leads) if leads else durations.manufacturing)
    if order.has_make:
        return max(1, durations.shipping)
    leads = [p.lead_time_days for p in order.parts if p.lead_time_days]
    return max(1, max(leads) if leads else durations.shipping)


def schedule_orders(orders: list[SupplierOrder], *, start: date,
                    durations: Durations) -> list[SupplierOrder]:
    """Fill in each order's stage dates, forward from ``start``.

    Every order starts on the same date; the stages within an order run back
    to back, which is what the finish-to-start dependencies then express in
    Wrike. Mutates and returns the orders.
    """
    for order in orders:
        order.schedule = []
        cursor = start
        for stage in order.stages:
            length = _stage_length(order, stage, durations)
            stage_start = add_business_days(cursor, 0)
            stage_due = add_business_days(stage_start, length - 1)
            order.schedule.append(
                StageSchedule(stage=stage, start=stage_start, due=stage_due))
            cursor = add_business_days(stage_due, 1)
    return orders


# ---------------------------------------------------------------------------
# Stage 5: titles and descriptions
# ---------------------------------------------------------------------------

STAGE_PARENT = "Parent"

# A plain hyphen with single spaces, not an em dash: the re-run guard compares
# parent titles literally, so the separator has to survive a round trip
# through the API unchanged.
TITLE_SEP = " - "


def parent_title(build: str, order: SupplierOrder) -> str:
    return f"{_text(build)}{TITLE_SEP}{order.supplier}"


def stage_title(build: str, order: SupplierOrder, stage: str) -> str:
    """Carries the build and supplier: a subtask appears detached from its
    parent in list views and in an assignee's My Work queue."""
    number = order.stages.index(stage) + 1
    return f"{_text(build)} {order.supplier}{TITLE_SEP}{number}. {stage}"


def _esc(value: Any) -> str:
    return html_lib.escape(_text(value))


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _qty(value: float) -> str:
    return f"{value:g}"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th align='left'>{_esc(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return (f"<table border='1' cellpadding='4' cellspacing='0'>"
            f"<tr>{head}</tr>{body}</table>")


def render_description(order: SupplierOrder, stage: str, *,
                       source_name: str) -> str:
    """The task body for one stage, as HTML.

    Wrike renders a description as HTML, so a plain string's newlines collapse
    and a part table arrives as one run-on line.
    """
    header = (f"<p><b>Supplier:</b> {_esc(order.supplier)}<br/>"
              f"<b>From:</b> {_esc(source_name)}</p>")

    if stage == STAGE_PARENT:
        rows = [[_esc(p.title), _esc(p.description), _qty(p.qty),
                 _esc(p.kind), _money(p.line_total)] for p in order.parts]
        return (header
                + f"<p>{len(order.parts)} line items, "
                  f"{_qty(order.piece_count)} pcs, "
                  f"{_money(order.total)} estimated.</p>"
                + _table(["Part", "Description", "Qty", "Kind", "Line total"],
                         rows))

    if stage == STAGE_PURCHASING:
        rows = [[_esc(p.title), _esc(p.description), _qty(p.qty),
                 _money(p.unit_cost), _money(p.line_total)]
                for p in order.parts]
        return (header
                + _table(["Part", "Description", "Qty", "Unit", "Line total"],
                         rows)
                + f"<p><b>Order total:</b> {_money(order.total)}</p>"
                + "<p>[ ] PO issued<br/>[ ] Acknowledgement received</p>")

    if stage == STAGE_MANUFACTURING:
        rows = [[_esc(p.title), _esc(p.description), _qty(p.qty),
                 _esc(p.revision), _esc(p.material)]
                for p in order.make_parts]
        return (header
                + _table(["Part", "Description", "Qty", "Rev", "Material"],
                         rows))

    rows = [[_esc(p.title), _esc(p.description), _qty(p.qty)]
            for p in order.parts]
    return (header
            + f"<p>Expect {_qty(order.piece_count)} pcs across "
              f"{len(order.parts)} line items.</p>"
            + _table(["Part", "Description", "Qty"], rows))


# ---------------------------------------------------------------------------
# Stage 6: create the tasks
# ---------------------------------------------------------------------------

@dataclass
class CreateResult:
    orders_created: int = 0
    orders_skipped: int = 0
    task_ids: list[str] = field(default_factory=list)
    skipped_titles: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    dependency_failures: list[str] = field(default_factory=list)
    check_errors: list[str] = field(default_factory=list)


def _rows_of(resp: dict[str, Any]) -> list[dict[str, Any]]:
    data = resp.get("data")
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, list):
            return [r for r in inner if isinstance(r, dict)]
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    return []


def _new_task_id(resp: dict[str, Any]) -> str:
    rows = _rows_of(resp)
    return _text(rows[0].get("id")) if rows else ""


async def _title_exists(wrike, folder_id: str, title: str) -> tuple[bool, Optional[str]]:
    """Whether the folder already holds a task with exactly this title.

    No status filter is passed: a completed order filtered out of the result
    would be recreated on the next run. Wrike's own title filter is a
    substring match, so the exact comparison happens here.

    Returns ``(exists, error)``. When the search itself errors, this fails
    closed — ``exists`` comes back True so the caller skips rather than risks
    a duplicate board, since Wrike has no rollback. ``error`` carries the
    failure detail (None on a real search) so the caller can tell that forced
    skip apart from a genuine match: both must skip, but only one of them is
    evidence the order is already there, and a report that conflated them
    would call a failed check a success.
    """
    resp = await wrike.search_tasks(title=title, folder_id=folder_id)
    if resp.get("error"):
        # An unreadable folder must not silently duplicate a board. Treat the
        # order as existing and let the caller report the skip.
        logger.warning("Existence check failed for %r: %s", title,
                       resp.get("data"))
        return True, _text(resp.get("data")) or "search failed"
    return any(_text(r.get("title")) == title for r in _rows_of(resp)), None


async def create_orders(
    wrike,
    *,
    folder_id: str,
    build: str,
    orders: list[SupplierOrder],
    owners: dict[str, str],
    source_name: str,
    on_progress: Optional[ProgressFn] = None,
) -> CreateResult:
    """Create one parent task plus its stage subtasks for every order.

    Serial, both across orders and within one: creation is cheap, and serial
    keeps the log readable and the API gentle.

    There is no rollback — Wrike has no transaction. A trio that fails halfway
    is reported with the ids that *were* created so it can be cleaned up by
    hand, and the loop moves to the next supplier.
    """
    progress: ProgressFn = on_progress or (lambda _msg: None)
    result = CreateResult()

    for order in orders:
        title = parent_title(build, order)

        exists, check_error = await _title_exists(wrike, folder_id, title)
        if exists:
            result.orders_skipped += 1
            if check_error:
                # Fail-closed skipped it, but nothing was actually checked or
                # created — reporting this as "already exists" would tell the
                # user a failed run was a clean no-op.
                result.check_errors.append(f"{title}: {check_error}")
                progress(f"  {title}: existence check failed - skipped as a "
                        f"precaution")
            else:
                result.skipped_titles.append(title)
                progress(f"  {title}: already exists - skipped")
            continue

        owner = owners.get(order.stages[0]) if order.stages else None
        parent_resp = await wrike.create_task(
            folder_id, title,
            description=render_description(order, STAGE_PARENT,
                                           source_name=source_name),
            start_date=order.start.isoformat() if order.start else None,
            due_date=order.due.isoformat() if order.due else None,
            responsibles=[owner] if owner else None,
        )
        parent_id = _new_task_id(parent_resp)
        if parent_resp.get("error") or not parent_id:
            result.failures.append(f"{title}: {parent_resp.get('data')}")
            progress(f"  {title}: FAILED - {parent_resp.get('data')}")
            continue

        result.task_ids.append(parent_id)
        progress(f"  {title}: created")

        by_stage = {s.stage: s for s in order.schedule}
        previous_id = ""
        previous_dated = False
        for stage in order.stages:
            sched = by_stage.get(stage)
            stage_owner = owners.get(stage)
            sub_title = stage_title(build, order, stage)
            resp = await wrike.create_task(
                folder_id, sub_title,
                description=render_description(order, stage,
                                               source_name=source_name),
                start_date=sched.start.isoformat() if sched else None,
                due_date=sched.due.isoformat() if sched else None,
                responsibles=[stage_owner] if stage_owner else None,
                super_task_ids=[parent_id],
            )
            task_id = _new_task_id(resp)
            if resp.get("error") or not task_id:
                result.failures.append(f"{sub_title}: {resp.get('data')}")
                progress(f"    {stage}: FAILED - {resp.get('data')}")
                continue

            result.task_ids.append(task_id)
            progress(f"    {stage}: created")

            # Wrike refuses a dependency between undated tasks, so linking an
            # unscheduled stage would fail every time. Skip rather than
            # generate a guaranteed error.
            if previous_id and sched is not None and previous_dated:
                dep = await wrike.add_dependency(task_id, previous_id)
                if dep.get("error"):
                    # The tasks are the product; the link is the garnish.
                    result.dependency_failures.append(
                        f"{sub_title}: {dep.get('data')}")
                    progress(f"    {stage}: dependency not linked")
            previous_id = task_id
            previous_dated = sched is not None

        result.orders_created += 1

    return result
