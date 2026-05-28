"""
Golden tests for the label inspection pipeline.

These tests do NOT require numpy / opencv / Gemini — they exercise the
pure-Python text matching and cache-guard layers only.

Run with:
    python3 -m pytest tests/test_inspection_golden.py -v
"""

import sys
import os
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from inspectors.master_loader import FieldSpec
from inspectors.text_compare import (
    _normalize_text,
    _find_anchor_candidate,
    _find_candidate,
    compare_field,
    overall_text_verdict,
)
from inspectors.master_ocr import _is_valid_ocr, _CACHE_VERSION


# ─────────────────────────────────────────────────────────────────────────────
# Layer 1: cache guard
# ─────────────────────────────────────────────────────────────────────────────

class TestIsValidOcr:
    def test_valid_result_passes(self):
        result = {
            "text": "A" * 50,
            "blocks": [{"text": f"block{i}", "bbox": None, "conf": 0.9}
                       for i in range(6)],
            "stub": False,
        }
        assert _is_valid_ocr(result) is True

    def test_stub_is_rejected(self):
        result = {"text": "hello world", "blocks": [{}] * 6, "stub": True}
        assert _is_valid_ocr(result) is False

    def test_parse_error_is_rejected(self):
        result = {"text": "A" * 50, "blocks": [{}] * 6, "parse_error": "bad JSON"}
        assert _is_valid_ocr(result) is False

    def test_too_few_blocks_rejected(self):
        result = {"text": "A" * 50, "blocks": [{}] * 3, "stub": False}
        assert _is_valid_ocr(result) is False

    def test_too_short_text_rejected(self):
        result = {"text": "hi", "blocks": [{}] * 6, "stub": False}
        assert _is_valid_ocr(result) is False

    def test_empty_blocks_list_rejected(self):
        result = {"text": "A" * 50, "blocks": [], "stub": False}
        assert _is_valid_ocr(result) is False

    def test_cache_version_constant_is_int(self):
        assert isinstance(_CACHE_VERSION, int) and _CACHE_VERSION >= 2


# ─────────────────────────────────────────────────────────────────────────────
# Layer 2a: _normalize_text
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalizeText:
    def test_digits_strips_spaces(self):
        assert _normalize_text("7 49350 08858 9", "digits") == "7493500885889"

    def test_digits_barcode_with_dashes(self):
        assert _normalize_text("749-350-088-589", "digits") == "749350088589"

    def test_digits_phone_plus(self):
        assert _normalize_text("+971 4 380 0999", "digits") == "97143800999"

    def test_lower(self):
        assert _normalize_text("  NET WEIGHT  ", "lower") == "net weight"

    def test_nospace(self):
        assert _normalize_text("Net  Weight: 140 gm", "nospace") == "NetWeight:140gm"

    def test_empty_normalize_passthrough(self):
        assert _normalize_text("hello", "") == "hello"

    def test_none_normalize_passthrough(self):
        assert _normalize_text("hello", None) == "hello"

    def test_empty_text_any_normalize(self):
        assert _normalize_text("", "digits") == ""


# ─────────────────────────────────────────────────────────────────────────────
# Layer 2b: _find_anchor_candidate
# ─────────────────────────────────────────────────────────────────────────────

OCR_SAMPLE = """\
Production, Expiry date and Importer/Exporter information
may be vary as per local regulations.
Product of Thailand
Chunk Tuna Light Meat First Grade
Ingredients:
Tuna, Soybean Oil (Non-GMO), Water, Salt
Fish type: Katsuwonus pelamis (Skipjack)
Net Weight: 140 gm
Drained Weight: 100gm
Thai Union Manufacturing Co., Ltd.
94/1 Sethakit 1 Road, Maesang Samut
Samut Sakhon 74000, Thailand
Importer & Distributor in Egypt:
Pure Company for Integrated Business Solutions
Bahgat Aly St, Al Zamalek, Cairo, P.O. Box 11211
Customer Services: +201320126185
Email: info@pureco.com
Importer & Distributor in UAE:
Pure Osho General Trading L.L.C
P.O. Box 35293 Dubai, UAE
Customer Services: +97143800999
Email: info@pureosho-trading.com
Nutrition information
Energy Kcal/KJ
170/710
Protein (g)
26
Fat (g)
4
Saturated fat (g)
1
Sodium (mg)
476
AQUA
PREMIUM
CHUNK
LIGHT MEAT TUNA
7 4933 5008 8589
"""


def _spec(anchor="", value_regex="", normalize="", expected="", method="levenshtein"):
    return FieldSpec(
        name="test", expected=expected, method=method,
        anchor=anchor, value_regex=value_regex, normalize=normalize,
    )


class TestFindAnchorCandidate:
    def test_anchor_with_value_on_same_line(self):
        spec = _spec(anchor="Net Weight")
        result = _find_anchor_candidate(spec, OCR_SAMPLE)
        assert result is not None
        assert "140" in result

    def test_anchor_value_on_next_line(self):
        spec = _spec(anchor="Ingredients")
        result = _find_anchor_candidate(spec, OCR_SAMPLE)
        assert result is not None
        assert "Tuna" in result

    def test_anchor_with_value_regex(self):
        spec = _spec(anchor="Sodium", value_regex=r"\d+")
        result = _find_anchor_candidate(spec, OCR_SAMPLE)
        assert result == "476"

    def test_anchor_regex_next_line(self):
        # "Energy Kcal/KJ" has no digits; value is on next line "170/710"
        spec = _spec(anchor="Energy", value_regex=r"\d+")
        result = _find_anchor_candidate(spec, OCR_SAMPLE)
        assert result == "170"

    def test_anchor_regex_kj_value(self):
        spec = _spec(anchor="Energy", value_regex=r"(?<=/)\d+")
        result = _find_anchor_candidate(spec, OCR_SAMPLE)
        assert result == "710"

    def test_anchor_protein(self):
        spec = _spec(anchor="Protein", value_regex=r"\d+")
        result = _find_anchor_candidate(spec, OCR_SAMPLE)
        assert result == "26"

    def test_anchor_fish_type(self):
        spec = _spec(anchor="Fish type")
        result = _find_anchor_candidate(spec, OCR_SAMPLE)
        assert result is not None
        assert "Katsuwonus" in result

    def test_anchor_not_found_returns_none(self):
        spec = _spec(anchor="NonExistentAnchorXYZ")
        result = _find_anchor_candidate(spec, OCR_SAMPLE)
        assert result is None

    def test_anchor_egypt_phone(self):
        spec = _spec(anchor="Egypt", value_regex=r"\+?20[\d]+")
        result = _find_anchor_candidate(spec, OCR_SAMPLE)
        assert result is not None
        assert "201320126185" in _normalize_text(result, "digits")

    def test_anchor_uae_phone(self):
        spec = _spec(anchor="UAE", value_regex=r"\+?971[\d]+")
        result = _find_anchor_candidate(spec, OCR_SAMPLE)
        assert result is not None
        assert "97143800999" in _normalize_text(result, "digits")


# ─────────────────────────────────────────────────────────────────────────────
# Layer 2c: compare_field end-to-end (no spatial blocks)
# ─────────────────────────────────────────────────────────────────────────────

class TestCompareField:
    # Barcode: normalize digits handles spaces
    def test_barcode_passes_with_spaced_ocr(self):
        spec = FieldSpec(
            name="barcode_ean13", expected="7493350088589",
            method="exact", tolerance=0, critical=True, normalize="digits",
        )
        result = compare_field(spec, OCR_SAMPLE)
        assert result.passed, f"found={result.found!r} distance={result.distance}"

    # Brand: short exact field found via substring
    def test_brand_aqua_found(self):
        spec = FieldSpec(
            name="brand", expected="AQUA",
            method="exact", tolerance=0, critical=True,
        )
        result = compare_field(spec, OCR_SAMPLE)
        assert result.passed, f"found={result.found!r}"

    # Net weight: anchor prevents picking wrong line
    def test_net_weight_with_anchor(self):
        spec = FieldSpec(
            name="net_weight", expected="Net Weight: 140 gm",
            method="levenshtein", tolerance=2, critical=True, anchor="Net Weight",
        )
        result = compare_field(spec, OCR_SAMPLE)
        assert result.passed, f"found={result.found!r} dist={result.distance}"

    # Ingredients: anchor picks correct next-line value
    def test_ingredients_with_anchor(self):
        spec = FieldSpec(
            name="ingredients",
            expected="Tuna, Soybean Oil (Non-GMO), Water, Salt",
            method="levenshtein", tolerance=4, critical=True, anchor="Ingredients",
        )
        result = compare_field(spec, OCR_SAMPLE)
        assert result.passed, f"found={result.found!r} dist={result.distance}"

    # Sodium: anchor + value_regex extracts exact number
    def test_sodium_exact_match(self):
        spec = FieldSpec(
            name="sodium_value", expected="476",
            method="exact", tolerance=0, critical=True,
            anchor="Sodium", value_regex=r"\d+",
        )
        result = compare_field(spec, OCR_SAMPLE)
        assert result.passed, f"found={result.found!r}"

    # Sodium wrong value should FAIL
    def test_sodium_wrong_value_fails(self):
        spec = FieldSpec(
            name="sodium_value", expected="999",
            method="exact", tolerance=0, critical=True,
            anchor="Sodium", value_regex=r"\d+",
        )
        result = compare_field(spec, OCR_SAMPLE)
        assert not result.passed
        assert result.severity == "critical"

    # Energy kcal
    def test_energy_kcal_extracted(self):
        spec = FieldSpec(
            name="energy_value", expected="170",
            method="exact", tolerance=0, critical=True,
            anchor="Energy", value_regex=r"\d+",
        )
        result = compare_field(spec, OCR_SAMPLE)
        assert result.passed, f"found={result.found!r}"

    # Energy kj
    def test_energy_kj_extracted(self):
        spec = FieldSpec(
            name="energy_kj", expected="710",
            method="exact", tolerance=0, critical=True,
            anchor="Energy", value_regex=r"(?<=/)\d+",
        )
        result = compare_field(spec, OCR_SAMPLE)
        assert result.passed, f"found={result.found!r}"

    # Fish species
    def test_fish_species_with_anchor(self):
        spec = FieldSpec(
            name="fish_species",
            expected="Katsuwonus pelamis (Skipjack)",
            method="levenshtein", tolerance=3, critical=True, anchor="Fish type",
        )
        result = compare_field(spec, OCR_SAMPLE)
        assert result.passed, f"found={result.found!r} dist={result.distance}"


# ─────────────────────────────────────────────────────────────────────────────
# Layer 2d: regression — wrong OCR detects difference
# ─────────────────────────────────────────────────────────────────────────────

OCR_DEFECTIVE = OCR_SAMPLE.replace(
    "Tuna, Soybean Oil (Non-GMO), Water, Salt",
    "Tuna, Palm Oil, Water, Salt",            # wrong oil
).replace(
    "7 49350 08858 9",
    "7 4933 5008 8580",                        # barcode last digit wrong
).replace(
    "476",
    "490",                                    # sodium value wrong
)


class TestDefectDetection:
    def test_ingredients_defect_detected(self):
        spec = FieldSpec(
            name="ingredients",
            expected="Tuna, Soybean Oil (Non-GMO), Water, Salt",
            method="levenshtein", tolerance=4, critical=True, anchor="Ingredients",
        )
        result = compare_field(spec, OCR_DEFECTIVE)
        assert not result.passed, "Should detect wrong oil type"

    def test_barcode_defect_detected(self):
        spec = FieldSpec(
            name="barcode_ean13", expected="7493350088589",
            method="exact", tolerance=0, critical=True, normalize="digits",
        )
        result = compare_field(spec, OCR_DEFECTIVE)
        assert not result.passed, "Should detect wrong last digit"

    def test_sodium_defect_detected(self):
        spec = FieldSpec(
            name="sodium_value", expected="476",
            method="exact", tolerance=0, critical=True,
            anchor="Sodium", value_regex=r"\d+",
        )
        result = compare_field(spec, OCR_DEFECTIVE)
        assert not result.passed, "Should detect wrong sodium value"

    def test_overall_verdict_fail_on_critical(self):
        from inspectors.text_compare import FieldResult
        results = [
            FieldResult("a", "x", "y", "exact", 1, False, True, "critical"),
            FieldResult("b", "x", "x", "exact", 0, True, True, "ok"),
        ]
        assert overall_text_verdict(results) == "FAIL"

    def test_overall_verdict_pass_all_ok(self):
        from inspectors.text_compare import FieldResult
        results = [
            FieldResult("a", "x", "x", "exact", 0, True, True, "ok"),
            FieldResult("b", "y", "y", "exact", 0, True, True, "ok"),
        ]
        assert overall_text_verdict(results) == "PASS"
