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
import logging
import os
from dataclasses import dataclass
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


def vendor_key(value: str) -> str:
    """Normalized form for comparing and grouping supplier names.

    Case, surrounding whitespace and runs of internal whitespace all collapse:
    the reference BOM spells it McMASTER-CARR, and a supplier typed as
    "machine  shop" is the same vendor as "Machine Shop".
    """
    return " ".join(_text(value).split()).casefold()


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
