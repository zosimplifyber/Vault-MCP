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
# The list was re-keyed on 2026-07-28: "Title (Name)" holds the CAD file name
# without its extension and is now THE key; the old part-number column was
# renamed "OLDPt.2-Title" and is written for provenance only, never matched on.
KEY_LIST_COLUMN = "Title (Name)"

_BOM_TO_LIST_DISPLAY = {
    "Name": KEY_LIST_COLUMN,                    # <- BOM file name, no extension
    "Description (Item,CO)": "Description",
    "Material": "Material",
    "Vendor": "Vendor",
    "Vendor Number": "Vendor Number",           # <- BOM "Web Link"
}


def _display_to_internal(client) -> dict[str, str]:
    return {display: internal
            for internal, display in client.column_display_map().items()}


def existing_index(client) -> dict[str, str]:
    """Normalized file name (no extension) -> list item id.

    Read from the list's "Title (Name)" column. Rows that have no name are not
    indexed: without a fallback key there is nothing to match them on.
    """
    key_field = _display_to_internal(client).get(KEY_LIST_COLUMN)
    out: dict[str, str] = {}
    if not key_field:
        return out
    for item_id, fields in client.iter_rows():
        name = normalize_part_number(fields.get(key_field))
        if name and name not in out:
            out[name] = item_id
    return out


def existing_numbers(client) -> set[str]:
    """Normalized file-name keys already in the list."""
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

    "Title (Name)" — the file name without its extension — is the key and comes
    from the BOM's Name column. The built-in ``Title`` field still receives the
    part number so a row is recognisable in list views, but nothing matches on
    it. Everything else comes from the matching BOM column.
    """
    fields: dict[str, object] = {"Title": number} if number else {}

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

    Rows are keyed on the file name without its extension — the list's
    "Title (Name)" column. A BOM row with no file name cannot be matched and is
    skipped (counted in ``skipped_no_name``) rather than guessed at from its
    part number.

    Missing parts are created. With ``update_existing=True``, parts already in
    the list are PATCHed with the BOM's descriptive fields (Description,
    Material, Vendor, Vendor Number) — the key and any Cost/Lead already entered
    are left untouched. ``sources`` optionally restricts to BOM rows whose
    Source is in the set; a row it excludes is counted in ``skipped_source``
    rather than silently dropped, so a caller can tell "the list is current"
    apart from "every row was filtered out before it could be checked" (the
    shape a BOM export with no Source column produces — every row's source
    reads as "" and matches nothing). Dry-run writes nothing.
    """
    d2i = _display_to_internal(client)
    index = existing_index(client)
    key_field = d2i.get(KEY_LIST_COLUMN)
    source_set = {str(s).strip() for s in sources} if sources else None

    rows_out: list[dict] = []
    missing: list[str] = []
    errors: list[dict] = []
    by_source: dict[str, int] = {}
    seen: set[str] = set()
    created = 0
    updated = 0
    already_present = 0
    skipped_no_name = 0
    skipped_source = 0

    has_source = "Source" in getattr(bom_df, "columns", [])
    for _idx, row in bom_df.iterrows():
        name = normalize_part_number(_row_get(row, "Name"))
        # The file name is the part number's equivalent when the BOM has none.
        number = normalize_part_number(_row_get(row, "Number")) or name
        source = str(_row_get(row, "Source")).strip() if has_source else ""
        if source_set is not None and source not in source_set:
            # Counted, not just dropped: a caller with no visibility into
            # how many rows the source filter ate cannot tell "the list is
            # current" apart from "every row was filtered before it could
            # be checked" — the shape a BOM export with no Source column
            # produces, since every row's source then reads as "".
            skipped_source += 1
            continue
        if not name:
            skipped_no_name += 1
            continue
        if name in seen:
            continue
        seen.add(name)
        if name in index:
            already_present += 1

        fields = build_item_fields(row, number, d2i)
        description = fields.get(d2i.get("Description", ""), None)
        entry = {"name": name, "number": number, "description": description,
                 "source": source}

        if name in index:
            if not update_existing:
                continue
            patch = {k: v for k, v in fields.items()
                     if k not in ("Title", key_field)}
            status = "would_update"
            if patch and not dry_run:
                try:
                    client.patch_fields(index[name], patch)
                    updated += 1
                    status = "updated"
                except Exception as exc:                   # noqa: BLE001
                    status = "error"
                    errors.append({"name": name, "number": number,
                                   "error": str(exc)})
            elif not patch:
                status = "unchanged"
            rows_out.append({**entry, "status": status, "fields": fields})
            continue

        missing.append(name)
        by_source[source or "(none)"] = by_source.get(source or "(none)", 0) + 1
        status = "would_add"
        if not dry_run:
            try:
                client.create_list_item(fields)
                created += 1
                status = "added"
            except Exception as exc:                       # noqa: BLE001
                status = "error"
                errors.append({"name": name, "number": number, "error": str(exc)})
        rows_out.append({**entry, "status": status, "fields": fields})

    return {
        "missing": missing,
        "skipped_no_name": skipped_no_name,
        "skipped_source": skipped_source,
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
# In the export template's column order. Filename leads the intent: its stem IS
# the lookup key, and with no part-number fallback a BOM without it cannot match
# anything. The rest are required because every one of them populates a column
# of the list row being created. "Required" means the COLUMN must exist — an
# individual cell may still be empty (REV and Vendor usually are on fasteners).
REQUIRED_BOM_FIELDS: dict[str, tuple[str, ...]] = {
    "Filename": ("filename", "file name", "file", "document name"),
    "BOM Structure": ("bom structure", "bomstructure", "source", "itemsource"),
    "QTY": ("qty", "quantity", "item qty"),
    "Description": ("description", "desc", "description (item,co)"),
    "REV": ("rev", "revision"),
    "Vendor": ("vendor", "supplier"),
    "Material": ("material",),
}

# Listed in the export template's own column order so the GUI reads like the
# spreadsheet. Title is deliberately absent — the template dropped it, and the
# code still reads one when an older export happens to carry it.
OPTIONAL_BOM_FIELDS: dict[str, tuple[str, ...]] = {
    # Nothing matches on the part number any more; it is written to the list's
    # legacy column when the export happens to carry it.
    "Part Number": ("part number", "partnumber", "number", "item number"),
    "Unit QTY": ("unit qty", "units", "unit", "uom"),
    "Web Link": ("web link", "weblink", "vendor number", "vendor #"),
}

# In the export, read by nothing — never reported as missing.
IGNORED_BOM_FIELDS: tuple[str, ...] = ("Thumbnail", "Item")


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


def default_bom_dir() -> str:
    """Where BOM exports live — the Vault working folder the purchasing sheet
    also writes into, or the fallback when that folder is not on this machine.
    One source of truth for the path, shared with bom_purchasing.
    """
    import bom_purchasing
    return bom_purchasing.default_output_dir()


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

    # An export without a Part Number column is still usable: the file name
    # without its extension stands in for it, which is what the list's legacy
    # column would otherwise be missing.
    file_col = next((c for c in _FILE_NAME_COLUMNS if c in raw.columns), None)
    has_number = any(c in raw.columns for c in ("Part Number", "Number"))
    if file_col is not None and not has_number:
        raw = raw.copy()
        raw["Part Number"] = raw[file_col].map(_file_stem)

    coerced, err = bom_purchasing.coerce_bom_dataframe(raw.copy())
    if err:
        return coerced, err
    # coerce_bom_dataframe reset_index(drop=True) without reordering rows, so the
    # raw frame aligns positionally — pull the dropped columns back by value.
    raw = raw.reset_index(drop=True)
    for src, dest in _RAW_EXTRA.items():
        if src in raw.columns:
            coerced[dest] = raw[src].values

    # The key: the CAD file name without its extension. Everything downstream
    # matches on this, so it is taken from the export's file-name column and
    # nowhere else — a row without one has no key. Same column resolved above.
    coerced["Name"] = (raw[file_col].map(_file_stem).values
                       if file_col is not None else None)
    return coerced, None
