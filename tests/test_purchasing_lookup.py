# tests/test_purchasing_lookup.py
"""Reference matching for the purchasing sheet.

The "Engineering Purchased Parts" list stores part numbers in their normalized
(upper-cased, single-spaced) form — that is what bom_list_sync writes — while an
Inventor BOM carries them as authored ("ISO 4762 - M6 x 10 - Stainless Steel").
The lookup has to bridge that, or a part that IS in the list is reported as
"not in the reference file" and ships with a blank Vendor / Cost.
"""
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bom_purchasing as bp  # noqa: E402


class TestFileNameIsTheKey:
    """The reference is keyed on the file name without its extension — the
    list's "Title (Name)" column — never on the part number."""

    def test_the_row_matches_on_its_file_name(self):
        bom = pd.DataFrame({"Number": ["SF-001944"], "Filename": ["CD-001625.ipt"],
                            "Source": ["Buy"]})
        ref = pd.DataFrame({"Number": ["CD-001625"], "Vendor": ["Acme"],
                            "Cost Per": [2.5]})
        out, matched, total = bp.lookup_purchased_data(bom, ref)
        assert out.loc[0, "Vendor"] == "Acme"
        assert (matched, total) == (1, 1)

    def test_the_part_number_is_not_a_fallback(self):
        # SF-001944 is in the reference, but the file name is not — no match.
        bom = pd.DataFrame({"Number": ["SF-001944"], "Filename": ["CD-999999.ipt"],
                            "Source": ["Buy"]})
        ref = pd.DataFrame({"Number": ["SF-001944"], "Vendor": ["Acme"]})
        out, matched, _ = bp.lookup_purchased_data(bom, ref)
        assert pd.isna(out.loc[0, "Vendor"])
        assert matched == 0

    def test_a_row_with_no_file_name_matches_nothing(self):
        bom = pd.DataFrame({"Number": ["SF-001944"], "Filename": [None],
                            "Source": ["Buy"]})
        ref = pd.DataFrame({"Number": ["SF-001944"], "Vendor": ["Acme"]})
        out, matched, _ = bp.lookup_purchased_data(bom, ref)
        assert pd.isna(out.loc[0, "Vendor"])
        assert matched == 0

    def test_case_and_spacing_still_do_not_matter(self):
        bom = pd.DataFrame({"Number": ["X"], "Source": ["Buy"],
                            "Filename": ["ISO 4762 - M6 x 50 - Steel.ipt"]})
        ref = pd.DataFrame({"Number": ["ISO 4762 - M6 X 50 - STEEL"],
                            "Vendor": ["McMASTER-CARR"]})
        out, matched, _ = bp.lookup_purchased_data(bom, ref)
        assert out.loc[0, "Vendor"] == "McMASTER-CARR"


def test_matches_a_reference_number_that_differs_only_by_case():
    bom = pd.DataFrame({"Number": ["X"], "Source": ["Buy"],
                        "Filename": ["ISO 4762 - M6 x 10 - Stainless Steel.ipt"]})
    ref = pd.DataFrame({"Number": ["ISO 4762 - M6 X 10 - STAINLESS STEEL"],
                        "Vendor": ["McMASTER-CARR"], "Cost Per": [0.19]})
    out, matched, total = bp.lookup_purchased_data(bom, ref)
    assert out.loc[0, "Vendor"] == "McMASTER-CARR"
    assert out.loc[0, "Cost Per"] == 0.19
    assert (matched, total) == (1, 1)


def test_matches_a_reference_number_with_collapsed_whitespace():
    bom = pd.DataFrame({"Number": ["X"], "Source": ["Buy"],
                        "Filename": ["ISO 10642 - M5 x  14.ipt"]})
    ref = pd.DataFrame({"Number": ["ISO 10642 - M5 X 14"],
                        "Vendor": ["McMASTER-CARR"], "Cost Per": [0.31]})
    out, matched, _ = bp.lookup_purchased_data(bom, ref)
    assert out.loc[0, "Vendor"] == "McMASTER-CARR"
    assert matched == 1


def test_leaves_a_part_that_is_absent_from_the_reference_unmatched():
    bom = pd.DataFrame({"Number": ["X"], "Source": ["Buy"],
                        "Filename": ["SF-999999.ipt"]})
    ref = pd.DataFrame({"Number": ["SF-000067"], "Vendor": ["Acme"]})
    out, matched, total = bp.lookup_purchased_data(bom, ref)
    assert pd.isna(out.loc[0, "Vendor"])
    assert (matched, total) == (0, 1)


def test_blank_part_numbers_never_match_a_blank_reference_row():
    bom = pd.DataFrame({"Number": ["X", "Y"], "Source": ["Buy", "Buy"],
                        "Filename": [None, float("nan")]})
    ref = pd.DataFrame({"Number": [float("nan"), "SF-000067"],
                        "Vendor": ["Ghost", "Acme"]})
    out, matched, _ = bp.lookup_purchased_data(bom, ref)
    assert out["Vendor"].isna().all()
    assert matched == 0


def test_first_reference_row_wins_when_two_normalize_to_the_same_number():
    bom = pd.DataFrame({"Number": ["X"], "Source": ["Buy"],
                        "Filename": ["SF-000067.ipt"]})
    ref = pd.DataFrame({"Number": ["SF-000067", "sf-000067"],
                        "Vendor": ["First", "Second"]})
    out, _, _ = bp.lookup_purchased_data(bom, ref)
    assert out.loc[0, "Vendor"] == "First"
