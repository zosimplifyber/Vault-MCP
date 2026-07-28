import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from supplier_pricing import normalize as nz  # noqa: E402


class TestVendorFamily:
    def test_mcmaster_variants_map_to_mcmaster(self):
        for name in ["McMaster Carr", "McMASTER-CARR", "McMaster-Carr",
                     "mcmaster carr", " McMaster  Carr "]:
            assert nz.vendor_family(name) == "mcmaster", name

    def test_misumi_variants_map_to_misumi(self):
        for name in ["MiSUMi", "MISUMI", "misumi", "Misumi "]:
            assert nz.vendor_family(name) == "misumi", name

    def test_unknown_vendor_returns_none(self):
        assert nz.vendor_family("Parker") is None
        assert nz.vendor_family("Yaodi") is None

    def test_blank_returns_none(self):
        assert nz.vendor_family("") is None
        assert nz.vendor_family(None) is None

    def test_is_supported_vendor(self):
        assert nz.is_supported_vendor("McMaster-Carr") is True
        assert nz.is_supported_vendor("MISUMI") is True
        assert nz.is_supported_vendor("Parker") is False


class TestNormalizePartNumber:
    def test_strips_and_uppercases(self):
        assert nz.normalize_part_number("  hfs5-2020-1000 ") == "HFS5-2020-1000"

    def test_strips_excel_float_suffix_on_numeric(self):
        # Excel infers a pure-numeric PN as a float -> "1078331.0"
        assert nz.normalize_part_number("1078331.0") == "1078331"

    def test_keeps_dot_zero_when_not_pure_numeric(self):
        # a real alphanumeric PN that happens to end in .0 must be preserved
        assert nz.normalize_part_number("2.00J2HHNAT124A16.000") == "2.00J2HHNAT124A16.000"

    def test_float_input_becomes_integer_string(self):
        assert nz.normalize_part_number(1078331.0) == "1078331"

    def test_collapses_internal_whitespace(self):
        assert nz.normalize_part_number("93115K  912") == "93115K 912"

    def test_none_and_blank(self):
        assert nz.normalize_part_number(None) == ""
        assert nz.normalize_part_number("   ") == ""


class TestLoosePartKey:
    def test_removes_separators_for_fuzzy_match(self):
        assert nz.loose_part_key("HFS5-2020-1000") == "HFS520201000"
        assert nz.loose_part_key("1078A331") == "1078A331"

    def test_case_insensitive(self):
        assert nz.loose_part_key("hfs5-2020") == nz.loose_part_key("HFS5 2020")


class TestFileStem:
    def test_strips_the_extension(self):
        assert nz.file_stem("CD-001578.ipt") == "CD-001578"

    def test_strips_only_the_last_extension(self):
        assert nz.file_stem("ISO 2338 - 5 h8 x 16 v2.ipt") == "ISO 2338 - 5 h8 x 16 v2"

    def test_drops_any_directory_part(self):
        assert nz.file_stem(r"C:\parts\CD-001578.ipt") == "CD-001578"

    def test_a_name_without_an_extension_is_kept(self):
        assert nz.file_stem("SF-001658") == "SF-001658"

    def test_blanks_and_nan_give_an_empty_key(self):
        assert nz.file_stem(None) == ""
        assert nz.file_stem(float("nan")) == ""
        assert nz.file_stem("  ") == ""
