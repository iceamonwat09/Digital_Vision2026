"""Unit tests for the Artwork Proof Check verification layers."""

import pytest

from artwork_check import checks
from artwork_check.checks import (check_group_consistency, check_numbers,
                                  check_phrases, check_readability,
                                  gs1_check_digit_ok, levenshtein)


def _zone(zid, ztype="panel", group="A", label=""):
    return {"id": zid, "type": ztype, "group": group,
            "bbox": [0.1, 0.1, 0.2, 0.2], "label": label or zid}


# ── Layer 1: cross-panel majority voting ──────────────────────────────

PANEL_OK = "HIDDEN BAY\nSKIPJACK TUNA\nPACKED IN WATER • SALT ADDED"
PANEL_TYPO = "HIDDEN BAY\nSKIPJAK TUNA\nPACKED IN WATER • SALT ADDED"


def test_voting_catches_one_bad_panel():
    zones = [_zone("z1"), _zone("z2"), _zone("z3"), _zone("z4")]
    texts = {"z1": PANEL_OK, "z2": PANEL_OK, "z3": PANEL_TYPO,
             "z4": PANEL_OK}
    defects = check_group_consistency(zones, texts)
    assert any(d["class"] == "MISMATCH_PANELS" and d["zone_id"] == "z3"
               for d in defects)
    # the typo line must be paired with the majority line
    hit = next(d for d in defects if d["zone_id"] == "z3" and d["found"])
    assert "SKIPJAK" in hit["found"]
    assert "SKIPJACK" in hit["reference"]


def test_voting_passes_identical_panels():
    zones = [_zone("z1"), _zone("z2"), _zone("z3")]
    texts = {z: PANEL_OK for z in ("z1", "z2", "z3")}
    assert check_group_consistency(zones, texts) == []


def test_voting_forgives_line_wrap_differences():
    zones = [_zone("z1"), _zone("z2"), _zone("z3")]
    texts = {
        "z1": "PACKED IN WATER\nSALT ADDED",
        "z2": "PACKED IN WATER\nSALT ADDED",
        "z3": "PACKED IN\nWATER SALT ADDED",   # same chars, rewrapped
    }
    assert check_group_consistency(zones, texts) == []


def test_voting_reports_missing_line():
    zones = [_zone("z1"), _zone("z2"), _zone("z3")]
    texts = {"z1": PANEL_OK, "z2": PANEL_OK,
             "z3": "HIDDEN BAY\nSKIPJACK TUNA"}   # last line missing
    defects = check_group_consistency(zones, texts)
    assert any(d["zone_id"] == "z3" and "หาย" in d["message"]
               for d in defects)


def test_zoom_mismatch_detected():
    zones = [_zone("z1"), _zone("z2"),
             _zone("zz", ztype="zoom", label="zoom-1")]
    texts = {"z1": "¡Para mejor calidad!", "z2": "¡Para mejor calidad!",
             "zz": "¡Para mejor caliddd"}
    defects = check_group_consistency(zones, texts)
    zoom_hits = [d for d in defects if d["class"] == "MISMATCH_ZOOM"]
    assert len(zoom_hits) == 1
    assert zoom_hits[0]["zone_id"] == "zz"
    assert "caliddd" in zoom_hits[0]["found"]


def test_zoom_partial_content_is_ok():
    zones = [_zone("z1"), _zone("z2"), _zone("zz", ztype="zoom")]
    texts = {"z1": PANEL_OK, "z2": PANEL_OK,
             "zz": "SKIPJACK TUNA"}     # zooms show only a part — fine
    assert check_group_consistency(zones, texts) == []


def test_zoom_forgives_ocr_punctuation_noise():
    """เคสจริงจากฉลาก Dolphin: zoom กับ panel พิมพ์เหมือนกันทุกตัวอักษร
    แต่ OCR สองรอบถอดเครื่องหมาย (เว้นวรรครอบขีด, en-dash, ตำแหน่ง %)
    ไม่เหมือนกัน — ต้องไม่ฟ้องผิด"""
    zones = [_zone("z8"), _zone("zz", ztype="zoom")]
    panel = ("Address : ELOBOUR-BLOCK 5 B - SQUARE 13026 - QALYUBIA, EGYPT\n"
             "الوزن المصفى ٧٠٪ من الوزن الصافي")
    zoom = ("Address : EL - OBOUR – BLOCK 5 B - SQUARE 13026 - "
            "QALYUBIA, EGYPT\n"
            "الوزن المصفى %٧٠% من الوزن الصافي")
    texts = {"z8": panel, "zz": zoom}
    assert check_group_consistency(zones, texts) == []


def test_zoom_forgives_merged_fields_and_rtl_order():
    """OCR ของ zoom รวม 2 ฟิลด์เป็นบรรทัดเดียว และอ่านคอลัมน์ RTL
    คนละลำดับกับ panel (รวมถึงถอดเลขอารบิก ١٦٧٨٥ ↔ 16785 ต่างกัน)"""
    zones = [_zone("z8"), _zone("zz", ztype="zoom")]
    panel = ("الخط الساخن : 16785\n"
             "المنتج : شركة تاي يونيون للتصنيع المحدودة")
    zoom = "المنتج : شركة تاي يونيون للتصنيع المحدودة الخط الساخن : ١٦٧٨٥"
    texts = {"z8": panel, "zz": zoom}
    assert check_group_consistency(zones, texts) == []


def test_zoom_real_digit_difference_still_flagged():
    """ความต่างของตัวเลขจริงต้องยังถูกจับ แม้หลังผ่อนเรื่องเครื่องหมาย"""
    zones = [_zone("z8"), _zone("zz", ztype="zoom")]
    texts = {"z8": "Hot Line : 16785", "zz": "Hot Line : 16786"}
    defects = check_group_consistency(zones, texts)
    assert any(d["class"] == "MISMATCH_ZOOM" for d in defects)


def test_voting_forgives_punctuation_ocr_noise():
    zones = [_zone("z1"), _zone("z2"), _zone("z3")]
    texts = {
        "z1": "EL - OBOUR – BLOCK 5 B",
        "z2": "ELOBOUR-BLOCK 5 B",
        "z3": "EL-OBOUR - BLOCK 5 B",
    }
    assert check_group_consistency(zones, texts) == []


def test_ungrouped_zones_not_compared():
    zones = [_zone("z1", group=""), _zone("z2", group="")]
    texts = {"z1": "AAA", "z2": "BBB"}
    assert check_group_consistency(zones, texts) == []


# ── Layer 2: numbers ──────────────────────────────────────────────────

def test_gs1_check_digit():
    assert gs1_check_digit_ok("0123456789012")       # EAN-13 valid
    assert not gs1_check_digit_ok("0123456789013")
    assert gs1_check_digit_ok("036000291452")        # UPC-A valid
    assert not gs1_check_digit_ok("036000291453")


def test_weight_math_pass():
    z = [_zone("z1", group="")]
    texts = {"z1": "6 • 43 OZ. POUCHES/BOLSAS\n"
                   "NET WT./PESO NETO 16.12 LBS. (7.31 kg)"}
    assert check_numbers(z, texts) == []


def test_weight_math_count_mismatch():
    z = [_zone("z1", group="")]
    texts = {"z1": "6 • 43 OZ. POUCHES\nNET WT. 17.50 LBS. (7.94 kg)"}
    defects = check_numbers(z, texts)
    assert any(d["class"] == "NUMBER_FAIL" and "16.125" in d["reference"]
               for d in defects)


def test_weight_math_kg_mismatch():
    z = [_zone("z1", group="")]
    texts = {"z1": "NET WT. 16.12 LBS. (9.99 kg)"}
    defects = check_numbers(z, texts)
    assert any(d["class"] == "NUMBER_FAIL" and "9.99" in d["found"]
               for d in defects)


def test_oz_gram_mismatch():
    z = [_zone("z1", group="")]
    texts = {"z1": "NET WT 5 OZ (199 g)"}    # 5 oz = 141.7 g
    defects = check_numbers(z, texts)
    assert any(d["class"] == "NUMBER_FAIL" for d in defects)


def test_oz_gram_pass():
    z = [_zone("z1", group="")]
    texts = {"z1": "NET WT 5 OZ (142 g)"}
    assert check_numbers(z, texts) == []


def test_barcode_in_text_with_spaces():
    z = [_zone("z1", group="")]
    # valid UPC-A with the spacing used in human-readable barcode text
    texts = {"z1": "0 36000 29145 2"}
    assert check_numbers(z, texts) == []
    texts_bad = {"z1": "0 36000 29145 9"}
    defects = check_numbers(z, texts_bad)
    assert any(d["class"] == "NUMBER_FAIL" and "036000291459" in d["message"]
               for d in defects)


# ── Layer 3: phrases (no invention — compare to approved strings) ─────

def test_phrase_near_miss_flagged():
    z = [_zone("z1", group="")]
    texts = {"z1": "ROTAR\n¡Para mejor caliddd"}
    defects = check_phrases(z, texts, ["¡Para mejor calidad!"])
    assert len(defects) == 1
    assert defects[0]["class"] == "PHRASE_FAIL"
    assert defects[0]["reference"] == "¡Para mejor calidad!"


def test_phrase_exact_passes():
    z = [_zone("z1", group="")]
    texts = {"z1": "ROTAR ¡Para mejor calidad! …"}
    assert check_phrases(z, texts, ["¡Para mejor calidad!"]) == []


def test_phrase_absent_is_not_a_defect():
    z = [_zone("z1", group="")]
    texts = {"z1": "completely unrelated text"}
    assert check_phrases(z, texts, ["¡Para mejor calidad!"]) == []


# ── Layer 3: dictionary (skipped when pyspellchecker absent) ──────────

@pytest.mark.skipif(not checks.spell_layer_available(),
                    reason="pyspellchecker not installed")
def test_spell_layer_flags_unknown_word_without_suggesting():
    z = [_zone("z1", group=""), _zone("z2", group="")]
    texts = {"z1": "para mejor caliddd", "z2": "packed in water"}
    defects = checks.check_spelling(z, texts)
    hits = [d for d in defects if "caliddd" in d["found"]]
    assert len(hits) == 1
    # the rule: never suggest a correction
    assert hits[0]["reference"] == ""
    assert "calidad" not in hits[0]["message"]


@pytest.mark.skipif(not checks.spell_layer_available(),
                    reason="pyspellchecker not installed")
def test_spell_layer_respects_brand_vocab():
    z = [_zone("z1", group="")]
    texts = {"z1": "HIDDEN BAY SKIPJACKK"}
    flagged = checks.check_spelling(z, texts, vocab_words=set())
    cleared = checks.check_spelling(z, texts, vocab_words={"SKIPJACKK"})
    assert any("SKIPJACKK" in d["found"] for d in flagged)
    assert cleared == []


# ── Layer 4: readability ──────────────────────────────────────────────

def test_readability_flags():
    zones = [_zone("z1", group=""), _zone("z2", group=""),
             _zone("z3", group="")]
    ocr = [
        {"zone_id": "z1", "text": "fine", "engine": "n8n", "conf": 0.9},
        {"zone_id": "z2", "text": "", "engine": "n8n", "conf": None},
        {"zone_id": "z3", "text": "blurry", "engine": "n8n", "conf": 0.3},
    ]
    defects = check_readability(zones, ocr)
    assert {d["zone_id"] for d in defects} == {"z2", "z3"}
    assert all(d["class"] == "UNREADABLE" for d in defects)


# ── misc ──────────────────────────────────────────────────────────────

def test_levenshtein_basic():
    assert levenshtein("calidad", "caliddd") == 1
    assert levenshtein("", "abc") == 3
    assert levenshtein("same", "same") == 0
