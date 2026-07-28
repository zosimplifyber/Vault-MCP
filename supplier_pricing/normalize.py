"""Vendor-name and part-number normalization.

The purchasing sheet stores supplier names inconsistently ("McMaster Carr",
"McMASTER-CARR", "McMaster-Carr") and part numbers can arrive from Excel with a
float-inferred ".0" suffix.  These helpers give us stable keys for matching.
"""
from __future__ import annotations

import os
import re

# Canonical vendor family -> set of normalized alias tokens.
# Aliases are compared after lower-casing and stripping non-alphanumerics.
_VENDOR_ALIASES: dict[str, set[str]] = {
    "mcmaster": {"mcmaster", "mcmastercarr", "mcmastercar"},
    "misumi": {"misumi", "misumiec"},
}


def _alnum_lower(value: object) -> str:
    """Lower-case and drop everything that is not a letter or digit."""
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def vendor_family(name: object) -> str | None:
    """Return the canonical vendor family ("mcmaster"/"misumi") or None.

    None means "not a supported/auto-priceable vendor" (e.g. Parker, Yaodi).
    """
    key = _alnum_lower(name)
    if not key:
        return None
    for family, aliases in _VENDOR_ALIASES.items():
        if key in aliases:
            return family
    return None


def is_supported_vendor(name: object) -> bool:
    """True when we have a provider that can auto-price this vendor."""
    return vendor_family(name) is not None


def supported_families() -> list[str]:
    return list(_VENDOR_ALIASES.keys())


def normalize_part_number(pn: object) -> str:
    """Normalize a part number for display/matching.

    - float/int inputs and Excel float-inferred numeric strings ("1078331.0")
      collapse to their integer string ("1078331").
    - alphanumeric part numbers are stripped, internal whitespace collapsed,
      and upper-cased; a trailing ".0" on a genuinely alphanumeric PN is kept.
    """
    if pn is None:
        return ""

    # Numeric types: render integral floats without the ".0".
    if isinstance(pn, bool):  # guard: bool is an int subclass
        return ""
    if isinstance(pn, (int, float)):
        if isinstance(pn, float) and pn.is_integer():
            return str(int(pn))
        return str(pn)

    text = str(pn).strip()
    if not text:
        return ""

    # Excel turns a pure-numeric PN into "1078331.0" — undo that, but only when
    # the whole token (minus the trailing .0) is digits.
    m = re.fullmatch(r"(\d+)\.0+", text)
    if m:
        text = m.group(1)

    text = re.sub(r"\s+", " ", text)
    return text.upper()


def file_stem(value: object) -> str:
    """A file name without its extension ("CD-001578.ipt" -> "CD-001578").

    The purchasing tools key on this: a BOM row's Part Number is the item
    number, while the file it refers to is named for the CAD document.
    """
    if value is None:
        return ""
    try:
        if value != value:            # NaN
            return ""
    except Exception:                 # noqa: BLE001
        pass
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return ""
    return os.path.splitext(os.path.basename(text))[0].strip()


def loose_part_key(pn: object) -> str:
    """Aggressive key for fuzzy matching: normalized, separators removed, upper."""
    return re.sub(r"[^A-Z0-9]", "", normalize_part_number(pn).upper())
