"""
Add BOM parts that aren't already in the "Engineering Purchased Parts" Microsoft
List.

Upload an exported BOM (Inventor/Vault .xlsx/.xls/.csv/.txt); this reads it with
``bom_purchasing`` (same header-mapping the purchasing sheet uses), compares each
part number against the List, and creates a new list item for every part that
isn't there yet. Dry-run by default.

Field mapping (confirmed live against the list):
  Title           <- Number (SF-xxxx)   # built-in key; drives the "Number" column
  field_1         <- Number             # "Title (Item,CO)" mirrors the number
  field_2         <- Description (Item,CO)
  field_3         <- Material
  field_4         <- Vendor (if the BOM carries one)
  field_5         <- Vendor Number (if present)
Internal names are resolved live from the list's columns, so they are not
hard-coded except for the built-in ``Title`` key.
"""
from __future__ import annotations

from typing import Iterable

from supplier_pricing.normalize import normalize_part_number

# BOM canonical column -> list column DISPLAY name (resolved to internal live).
_BOM_TO_LIST_DISPLAY = {
    "Description (Item,CO)": "Description (Item,CO)",
    "Material": "Material",
    "Vendor": "Vendor",
    "Vendor Number": "Vendor Number",
}


def _display_to_internal(client) -> dict[str, str]:
    return {display: internal
            for internal, display in client.column_display_map().items()}


def existing_numbers(client) -> set[str]:
    """Normalized part numbers already in the list (from the Title key field)."""
    out: set[str] = set()
    for _item_id, fields in client.iter_rows():
        num = normalize_part_number(fields.get("Title"))
        if num:
            out.add(num)
    return out


def _row_get(row, key):
    val = row.get(key) if hasattr(row, "get") else row[key]
    return val


def _is_blank(value) -> bool:
    """True for None, empty string, or a pandas/numpy NaN (NaN != NaN)."""
    if value is None:
        return True
    try:
        if value != value:          # NaN
            return True
    except Exception:
        pass
    s = str(value).strip()
    return s == "" or s.lower() == "nan"


def build_item_fields(row, number: str, d2i: dict[str, str]) -> dict:
    """Build the Graph `fields` body for a new list item from a BOM row."""
    fields: dict[str, object] = {"Title": number}
    # "Title (Item,CO)" mirrors the number in existing data.
    f1 = d2i.get("Title (Item,CO)")
    if f1:
        fields[f1] = number
    for bom_col, display in _BOM_TO_LIST_DISPLAY.items():
        internal = d2i.get(display)
        if not internal:
            continue
        try:
            value = _row_get(row, bom_col)
        except (KeyError, IndexError):
            value = None
        if not _is_blank(value):
            # List columns we populate here are all text -> stringify so numpy/
            # pandas scalars stay JSON-serializable.
            fields[internal] = str(value).strip()
    return fields


def add_missing_bom_rows(client, bom_df, *, dry_run: bool = True,
                         sources: Iterable[str] | None = None) -> dict:
    """Plan (and optionally create) list items for BOM parts not already present.

    ``client`` implements ``column_display_map()``, ``iter_rows()`` and
    ``create_list_item(fields)``. ``sources`` optionally restricts to BOM rows
    whose Source is in the set (e.g. {"Buy"}). Dry-run writes nothing.
    """
    d2i = _display_to_internal(client)
    have = existing_numbers(client)
    source_set = {str(s).strip() for s in sources} if sources else None

    rows_out: list[dict] = []
    missing: list[str] = []
    errors: list[dict] = []
    by_source: dict[str, int] = {}
    seen: set[str] = set()
    created = 0

    has_source = "Source" in getattr(bom_df, "columns", [])
    for _idx, row in bom_df.iterrows():
        number = normalize_part_number(_row_get(row, "Number"))
        if not number:
            continue
        source = str(_row_get(row, "Source")).strip() if has_source else ""
        if source_set is not None and source not in source_set:
            continue
        if number in have or number in seen:
            continue
        seen.add(number)
        missing.append(number)
        by_source[source or "(none)"] = by_source.get(source or "(none)", 0) + 1

        fields = build_item_fields(row, number, d2i)
        status = "would_add"
        if not dry_run:
            try:
                client.create_list_item(fields)
                created += 1
                status = "added"
            except Exception as exc:                       # noqa: BLE001
                status = "error"
                errors.append({"number": number, "error": str(exc)})
        rows_out.append({
            "number": number,
            "description": fields.get(d2i.get("Description (Item,CO)", ""), None),
            "source": source,
            "status": status,
            "fields": fields,
        })

    return {
        "missing": missing,
        "existing_count": len(have),
        "created": created,
        "errors": errors,
        "by_source": by_source,
        "rows": rows_out,
        "dry_run": dry_run,
    }


def bom_dataframe_from_file(path: str):
    """Read + header-map a BOM file into the canonical DataFrame (reuses
    bom_purchasing). Returns (df, error_message)."""
    import bom_purchasing
    raw = bom_purchasing.read_bom_file(path)
    return bom_purchasing.coerce_bom_dataframe(raw)
