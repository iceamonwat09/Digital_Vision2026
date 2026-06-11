"""
Tests for inspectors/barcode.py and the barcode override in text_compare.

Decoding real barcodes needs an optional backend (pyzbar / cv2.barcode), so
these tests focus on the always-available logic: digit normalisation, best
match selection, field-type detection, graceful no-backend behaviour, and
the compare_field override that prefers a decoded value over OCR.
"""

import numpy as np
import cv2
import pytest

from inspectors import barcode
from inspectors.text_compare import compare_field, _is_barcode_field
from inspectors.master_loader import FieldSpec


class TestDigitsOnly:
    def test_strips_non_digits(self):
        assert barcode.digits_only("749-335 0088589 x") == "7493350088589"

    def test_empty(self):
        assert barcode.digits_only("") == ""
        assert barcode.digits_only(None) == ""


class TestBestMatch:
    def test_exact_digit_match_wins(self):
        decoded = [{"data": "0000000000000"}, {"data": "749 3350088589"}]
        assert barcode.best_match(decoded, "7493350088589") == "749 3350088589"

    def test_empty_returns_none(self):
        assert barcode.best_match([], "123") is None

    def test_near_miss_longest_prefix(self):
        decoded = [{"data": "7493350088580"}, {"data": "1000000000000"}]
        assert barcode.best_match(decoded, "7493350088589") == "7493350088580"


class TestDecodeBarcodes:
    def test_empty_bytes(self):
        assert barcode.decode_barcodes(b"") == []

    def test_undecodable_bytes(self):
        assert barcode.decode_barcodes(b"not an image") == []

    def test_blank_image_no_barcode(self):
        blank = np.full((100, 200, 3), 255, np.uint8)
        ok, buf = cv2.imencode(".jpg", blank)
        assert barcode.decode_barcodes(bytes(buf)) == []


class TestIsBarcodeField:
    def test_name_match(self):
        assert _is_barcode_field(FieldSpec(name="barcode_ean13", expected="x"))

    def test_digit_exact_length_match(self):
        assert _is_barcode_field(FieldSpec(
            name="ean", expected="7493350088589", method="exact",
            normalize="digits"))

    def test_plain_text_field_not_barcode(self):
        assert not _is_barcode_field(FieldSpec(
            name="brand", expected="AQUA", method="exact"))

    def test_short_number_not_barcode(self):
        assert not _is_barcode_field(FieldSpec(
            name="energy", expected="170", method="exact", normalize="digits"))


class TestCompareFieldOverride:
    def test_decoded_overrides_wrong_ocr(self):
        spec = FieldSpec(name="barcode_ean13", expected="7493350088589",
                         tolerance=0, method="exact", critical=True,
                         normalize="digits")
        r = compare_field(spec, "junk 7493350000000 junk",
                          decoded_barcodes=[{"data": "749 3350088589"}])
        assert r.found == "749 3350088589"
        assert r.passed
        assert "barcode" in r.method

    def test_no_decode_falls_back_to_ocr(self):
        spec = FieldSpec(name="barcode_ean13", expected="7493350088589",
                         tolerance=0, method="exact", critical=True,
                         normalize="digits")
        r = compare_field(spec, "7493350088589", decoded_barcodes=None)
        assert r.passed             # OCR path still works
        assert "barcode" not in r.method

    def test_non_barcode_field_ignores_decode(self):
        spec = FieldSpec(name="brand", expected="AQUA", method="exact",
                         critical=True)
        r = compare_field(spec, "AQUA", decoded_barcodes=[{"data": "123"}])
        assert r.method == "exact"
        assert r.passed
