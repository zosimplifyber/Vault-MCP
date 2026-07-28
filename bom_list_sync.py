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

import os
from typing import Iterable

from supplier_pricing.normalize import normalize_part_number

# BOM canonical column -> list column DISPLAY name (resolved to internal live).
# The BOM's own Title / Vendor / Web Link are surfaced under these canonical
# names by bom_dataframe_from_file (coerce_bom_dataframe drops them).
_BOM_TO_LIST_DISPLAY = {
    "Title (Item,CO)": "Title (Item,CO)",       # <- BOM "Title" (NOT the part number)
    "Description (Item,CO)": "Description (Item,CO)",
    "Material": "Material",
    "Vendor": "Vendor",
    "Vendor Number": "Vendor Number",           # <- BOM "Web Link"
}


def _display_to_internal(client) -> dict[str, str]:
    return {display: internal
            for internal, display in client.column_display_map().items()}


def existing_index(client) -> dict[str, str]:
    """Normalized part number -> list item id (from the Title key field)."""
    out: dict[str, str] = {}
    for item_id, fields in client.iter_rows():
        num = normalize_part_number(fields.get("Title"))
        if num and num not in out:
            out[num] = item_id
    return out


def existing_numbers(client) -> set[str]:
    """Normalized part numbers already in the list."""
    return set(existing_index(client))


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
    """Build the Graph `fields` body for a new list item from a BOM row.

    The built-in ``Title`` field holds the part number (it drives the list's
    read-only "Number" column). Everything else comes from the matching BOM
    column — importantly, "Title (Item,CO)" is the BOM's own Title, not the number.
    """
    fields: dict[str, object] = {"Title": number}
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
                         sources: Iterable[str] | None = None,
                         update_existing: bool = False) -> dict:
    """Plan (and optionally apply) list changes from a BOM.

    Missing parts are created. With ``update_existing=True``, parts already in
    the list are PATCHed with the BOM's descriptive fields (Title (Item,CO),
    Description, Material, Vendor, Vendor Number) — the Title key and any
    Cost/Lead already entered are left untouched. ``sources`` optionally
    restricts to BOM rows whose Source is in the set. Dry-run writes nothing.
    """
    d2i = _display_to_internal(client)
    index = existing_index(client)
    source_set = {str(s).strip() for s in sources} if sources else None

    rows_out: list[dict] = []
    missing: list[str] = []
    errors: list[dict] = []
    by_source: dict[str, int] = {}
    seen: set[str] = set()
    created = 0
    updated = 0
    already_present = 0

    has_source = "Source" in getattr(bom_df, "columns", [])
    for _idx, row in bom_df.iterrows():
        number = normalize_part_number(_row_get(row, "Number"))
        if not number or number in seen:
            continue
        source = str(_row_get(row, "Source")).strip() if has_source else ""
        if source_set is not None and source not in source_set:
            continue
        seen.add(number)
        if number in index:
            already_present += 1

        fields = build_item_fields(row, number, d2i)
        description = fields.get(d2i.get("Description (Item,CO)", ""), None)

        if number in index:
            if not update_existing:
                continue
            patch = {k: v for k, v in fields.items() if k != "Title"}
            status = "would_update"
            if patch and not dry_run:
                try:
                    client.patch_fields(index[number], patch)
                    updated += 1
                    status = "updated"
                except Exception as exc:                   # noqa: BLE001
                    status = "error"
                    errors.append({"number": number, "error": str(exc)})
            elif not patch:
                status = "unchanged"
            rows_out.append({"number": number, "description": description,
                             "source": source, "status": status, "fields": fields})
            continue

        missing.append(number)
        by_source[source or "(none)"] = by_source.get(source or "(none)", 0) + 1
        status = "would_add"
        if not dry_run:
            try:
                client.create_list_item(fields)
                created += 1
                status = "added"
            except Exception as exc:                       # noqa: BLE001
                status = "error"
                errors.append({"number": number, "error": str(exc)})
        rows_out.append({"number": number, "description": description,
                         "source": source, "status": status, "fields": fields})

    return {
        "missing": missing,
        # BOM-side counts (how many parts this BOM contributed) vs the list-side
        # existing_count (how big the list is) — callers report them separately.
        "checked": len(seen),
        "already_present": already_present,
        "existing_count": len(index),
        "created": created,
        "updated": updated,
        "errors": errors,
        "by_source": by_source,
        "rows": rows_out,
        "dry_run": dry_run,
    }


# Raw BOM column -> canonical column, for fields coerce_bom_dataframe drops.
_RAW_EXTRA = {
    "Title": "Title (Item,CO)",     # the item's title (distinct from the number)
    "Vendor": "Vendor",
    "Web Link": "Vendor Number",     # owner's mapping: Vendor Number <- Web Link
}

# Where the export records the CAD file name, in the spellings Inventor uses.
_FILE_NAME_COLUMNS = ("Filename", "File Name", "File", "Document Name")


# --------------------------------------------------------------------------- columns
# What the sync reads out of a BOM export, and the headers that satisfy each
# field. Required fields are the ones without which the export cannot be read
# at all; the rest only decide how much of a new list row gets filled in.
REQUIRED_BOM_FIELDS: dict[str, tuple[str, ...]] = {
    "Part Number": ("part number", "partnumber", "number", "item number"),
    "QTY": ("qty", "quantity", "item qty"),
}

OPTIONAL_BOM_FIELDS: dict[str, tuple[str, ...]] = {
    "BOM Structure": ("bom structure", "bomstructure", "source", "itemsource"),
    "Title": ("title", "title (item,co)"),
    "Description": ("description", "desc", "description (item,co)"),
    "Material": ("material",),
    "Vendor": ("vendor", "supplier"),
    "Web Link": ("web link", "weblink", "vendor number", "vendor #"),
    "Filename": ("filename", "file name", "file", "document name"),
}


def check_bom_columns(headers: Iterable[str]) -> dict:
    """Which of the fields the sync uses are present in these headers.

    Returns ``{"ok", "missing_required", "missing_optional"}``. ``ok`` is False
    only when a *required* field is absent — optional gaps just mean the new
    list rows carry less detail.
    """
    have = {str(h).strip().lower() for h in headers if str(h).strip()}

    def missing(spec: dict[str, tuple[str, ...]]) -> list[str]:
        return [field for field, aliases in spec.items()
                if not have.intersection(aliases)]

    missing_required = missing(REQUIRED_BOM_FIELDS)
    return {
        "ok": not missing_required,
        "missing_required": missing_required,
        "missing_optional": missing(OPTIONAL_BOM_FIELDS),
    }


def bom_file_columns(path: str) -> list[str]:
    """The header row of a BOM export, without reading its data rows."""
    import pandas as pd
    ext = os.path.splitext(path)[1].lower()
    if ext in (".csv", ".txt"):
        head = pd.read_csv(path, sep="\t" if ext == ".txt" else ",", nrows=0)
    elif ext in (".xls", ".xlsx"):
        head = pd.read_excel(path, sheet_name=0, nrows=0)
    else:
        raise ValueError(f"Unsupported file type: {ext}. Use .xlsx, .xls, .csv, or .txt.")
    return [str(c).strip() for c in head.columns]


def _file_stem(value) -> str:
    """A file name without its extension ("CD-001578.ipt" -> "CD-001578")."""
    if _is_blank(value):
        return ""
    return os.path.splitext(str(value).strip())[0].strip()


def bom_dataframe_from_file(path: str):
    """Read + header-map a BOM file into the canonical DataFrame (reuses
    bom_purchasing), then re-attach the Title / Vendor / Web Link columns that
    coerce_bom_dataframe drops. Returns (df, error_message)."""
    import bom_purchasing
    raw = bom_purchasing.read_bom_file(path)
    coerced, err = bom_purchasing.coerce_bom_dataframe(raw.copy())
    if err:
        return coerced, err
    # coerce_bom_dataframe reset_index(drop=True) without reordering rows, so the
    # raw frame aligns positionally — pull the dropped columns back by value.
    raw = raw.reset_index(drop=True)
    for src, dest in _RAW_EXTRA.items():
        if src in raw.columns:
            coerced[dest] = raw[src].values

    # Parts whose Vault file carries no Title (every library fastener) would land
    # in the list with a blank Title. Fall back to the file name without its
    # extension, which is what a person would call the part anyway.
    file_col = next((c for c in _FILE_NAME_COLUMNS if c in raw.columns), None)
    if file_col is not None:
        stems = raw[file_col].map(_file_stem)
        if "Title (Item,CO)" not in coerced.columns:
            coerced["Title (Item,CO)"] = stems.values
        else:
            # An all-empty Title column reads as float64; cast to object before
            # writing strings into it.
            titles = coerced["Title (Item,CO)"].astype(object)
            keep = ~titles.map(_is_blank)
            coerced["Title (Item,CO)"] = titles.where(keep, stems.values)
    return coerced, None
