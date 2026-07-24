"""Datasource-agnostic value objects for supplier pricing.

`PriceResult` is what a provider (McMaster) or a parsed quote yields; the
datasource layer (SharePoint List / Excel) consumes it to update prices.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class PriceBreak:
    min_qty: int
    unit_price: float


@dataclass
class PriceResult:
    part_number: str
    vendor: str
    unit_price: float | None
    currency: str = "USD"
    price_breaks: list[PriceBreak] = field(default_factory=list)
    lead_time_days: int | None = None
    lead_time_text: str | None = None
    in_stock: bool | None = None
    source: str = ""            # "mcmaster:api" | "mcmaster:web" | "quote:<file>"
    source_url: str | None = None
    fetched_at: str | None = None      # ISO8601 UTC
    raw: dict | None = None
    error: str | None = None

    def ok(self) -> bool:
        return self.unit_price is not None and not self.error

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class QuoteLineItem:
    """One line parsed out of a supplier quote file."""
    part_number: str
    unit_price: float | None
    lead_time_days: int | None = None
    currency: str = "USD"
    description: str | None = None
    vendor: str | None = None
    qty: int | None = None
    source_file: str | None = None

    def as_price_result(self) -> PriceResult:
        label = self.source_file or "file"
        return PriceResult(
            part_number=self.part_number,
            vendor=self.vendor or "",
            unit_price=self.unit_price,
            currency=self.currency,
            lead_time_days=self.lead_time_days,
            source=f"quote:{label}",
            fetched_at=_utcnow_iso(),
        )


class UpdateOutcome:
    """Status of applying a price to one datasource row."""
    MATCHED = "matched"        # row found and written
    NOT_FOUND = "not_found"    # no row for this part number
    AMBIGUOUS = "ambiguous"    # more than one row matched
    UNCHANGED = "unchanged"    # matched but value already equal / nothing to write
