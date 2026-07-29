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

import logging
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
