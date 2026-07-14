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
    # The printed real labels carry the typo; the zoom is the correct,
    # human-readable reference. The defect must be attributed to the REAL
    # LABEL (the artwork that gets printed and corrected), with the zoom
    # as the reference of what it should say.
    zones = [_zone("z1"), _zone("z2"),
             _zone("zz", ztype="zoom", label="zoom-1")]
    texts = {"z1": "¡Para mejor caliddd", "z2": "¡Para mejor caliddd",
             "zz": "¡Para mejor calidad!"}
    defects = check_group_consistency(zones, texts)
    zoom_hits = [d for d in defects if d["class"] == "MISMATCH_ZOOM"]
    assert zoom_hits
    assert zoom_hits[0]["zone_id"] in ("z1", "z2")     # real label, not zoom
    assert "caliddd" in zoom_hits[0]["found"]          # the printed typo
    assert "calidad" in zoom_hits[0]["reference"]      # zoom = correct ref
    assert zoom_hits[0]["ref_zone_ids"] == ["zz"]


def test_zoom_pure_symbol_line_ignored():
    """OCR ของ zoom จับสัญลักษณ์ลูกศร ↑ (จากการลากโซนเกิน) มาเป็นบรรทัด
    หนึ่ง — ต้องไม่ถูกจับคู่กับคำจริงบนฉลาก (เช่น TUNA) จนฟ้องผิด"""
    zones = [_zone("z4"), _zone("zz", ztype="zoom")]
    panel = "Dolphin\nTUNA\nSHREDDED CHILI"
    zoom = "Dolphin\nTUNA\nSHREDDED CHILI\n↑"
    texts = {"z4": panel, "zz": zoom}
    assert check_group_consistency(zones, texts) == []


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


@pytest.mark.skipif(not checks.spell_layer_available(),
                    reason="pyspellchecker not installed")
def test_spell_layer_checks_cyrillic_and_arabic_words():
    # "рыба" (Russian, "fish") and "سمك" (Arabic, "fish") are valid
    # dictionary words and must not be flagged once ru/ar are enabled.
    z = [_zone("z1", group="")]
    texts = {"z1": "рыба سمك"}
    defects = checks.check_spelling(z, texts)
    assert defects == []


def test_re_word_extracts_cyrillic_and_arabic_tokens():
    text = "EN рыба سمك word"
    words = checks._RE_WORD.findall(text)
    assert "рыба" in words
    assert "سمك" in words


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


# ── snap_bbox (ดับเบิลคลิกให้กรอบพอดีเนื้อหา) ─────────────────────────

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")


def _page(dark=False):
    """หน้า 400×400: พื้นขาว (หรือกลับขั้วเป็นพื้นกรมท่า)"""
    from artwork_check.zones import snap_bbox  # noqa: F401 (import check)
    bg = (25, 35, 60) if dark else (255, 255, 255)
    img = np.zeros((400, 400, 3), np.uint8)
    img[:] = bg
    return img


def test_snap_shrinks_loose_box_to_panel():
    """กรอบหลวมรอบ panel แดงบนหน้าขาว → หดเข้าหาขอบ panel"""
    from artwork_check.zones import snap_bbox
    img = _page()
    img[150:260, 120:280] = (40, 40, 180)          # panel แดง
    snapped = snap_bbox(img, [0.20, 0.30, 0.55, 0.40])
    x, y, w, h = snapped
    assert abs(x - 120 / 400) < 0.02 and abs(y - 150 / 400) < 0.02
    assert abs((x + w) - 280 / 400) < 0.02
    assert abs((y + h) - 260 / 400) < 0.02


def test_snap_works_on_dark_artwork():
    """ขั้วสีกลับ: บล็อกตัวหนังสือขาวบนพื้นเข้ม → ยังหาขอบได้"""
    from artwork_check.zones import snap_bbox
    img = _page(dark=True)
    img[180:230, 100:300] = (245, 245, 245)        # ข้อความสีขาว
    snapped = snap_bbox(img, [0.15, 0.35, 0.65, 0.30])
    x, y, w, h = snapped
    assert abs(x - 100 / 400) < 0.02 and abs(y - 180 / 400) < 0.02
    assert abs((x + w) - 300 / 400) < 0.02
    assert abs((y + h) - 230 / 400) < 0.02


def test_snap_repeated_recovers_cut_content():
    """กรอบที่ตัดท้าย panel — ดับเบิลคลิกซ้ำหลายครั้งต้องขยายจนครบ"""
    from artwork_check.zones import snap_bbox
    img = _page()
    img[150:300, 120:280] = (40, 40, 180)
    b = [0.30, 0.375, 0.40, 0.25]                  # ตัดล่าง panel ออก ~25%
    for _ in range(10):
        b = snap_bbox(img, b)
    assert abs((b[1] + b[3]) - 300 / 400) < 0.03   # ขอบล่างกลับมาครบ


def test_snap_no_content_returns_original():
    """กรอบบนพื้นเปล่า → คืน bbox เดิม ไม่พัง"""
    from artwork_check.zones import snap_bbox
    img = _page()
    b = [0.05, 0.05, 0.10, 0.10]
    assert snap_bbox(img, b) == [round(v, 5) for v in b]


# ── translate table (advisory tab — must not touch the verdict) ───────

def test_build_table_skips_ignore_and_blank_lines():
    from artwork_check import translate
    zones = [_zone("z1", group=""), _zone("z9", ztype="ignore", group="")]
    ocr = [{"zone_id": "z1", "text": "Product of Thailand\n\nNet Weight"},
           {"zone_id": "z9", "text": "COLOR BAR"}]
    rows = translate.build_table(zones, ocr)
    assert all(r["zone_id"] != "z9" for r in rows)      # ignore excluded
    assert [r["src"] for r in rows] == ["Product of Thailand", "Net Weight"]


def test_build_table_flags_unknown_word_with_consensus_suggestion():
    from artwork_check import translate
    if not checks.spell_layer_available():
        pytest.skip("pyspellchecker not installed")
    zones = [_zone("z1", group="A"), _zone("z2", group="A")]
    ocr = [{"zone_id": "z1", "text": "Para mejor caliddd"},
           {"zone_id": "z2", "text": "Para mejor calidad"}]
    rows = translate.build_table(zones, ocr)
    bad = [r for r in rows if "caliddd" in r["src"]][0]
    assert bad["status"] == "spell"
    assert "caliddd" in bad["flagged"]
    # a confident single-answer typo keeps its dictionary suggestion
    assert "calidad" in bad["suggest"].get("caliddd", [])


def test_build_table_drops_scatter_dict_guesses():
    # "Cude" sits at edit-distance 1 from many unrelated words across
    # languages (code/cute/cure/crude/rude/...). The dictionary cannot say
    # which is intended, so the column must FLAG it but suggest nothing —
    # not the old misleading "aude cade cede". Detection is unchanged.
    from artwork_check import translate
    if not checks.spell_layer_available():
        pytest.skip("pyspellchecker not installed")
    zones = [_zone("z1", group="")]
    ocr = [{"zone_id": "z1", "text": "Cude Protein"}]
    rows = translate.build_table(zones, ocr)
    bad = [r for r in rows if "Cude" in r["src"]][0]
    assert bad["status"] == "spell"                 # still flagged
    assert "Cude" in bad["flagged"]
    assert bad["suggest"].get("Cude", []) == []     # no scatter guess


def test_build_table_respects_vocab():
    from artwork_check import translate
    if not checks.spell_layer_available():
        pytest.skip("pyspellchecker not installed")
    zones = [_zone("z1", group="")]
    ocr = [{"zone_id": "z1", "text": "SKIPJACKK brand"}]
    rows = translate.build_table(zones, ocr, vocab_words={"SKIPJACKK"})
    assert all(r["status"] == "ok" for r in rows)        # whitelisted


def test_translate_lines_alignment(monkeypatch):
    from artwork_check import translate

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {
                "translations": ["a", "b"],   # too few
                "spell": [{"flagged": True, "suggestion": "A"}],  # too few
            }

    monkeypatch.setattr(translate.requests, "post",
                        lambda *a, **k: FakeResp())
    monkeypatch.setattr(translate.config, "N8N_TRANSLATE_WEBHOOK_URL",
                        "http://x/webhook/artwork-translate")
    out = translate.translate_lines(["1", "2", "3"])
    assert out["translations"] == ["a", "b", ""]          # padded to len 3
    assert out["spell_available"] is True
    assert out["spell"] == [
        {"flagged": True, "suggestion": "A", "kind": None, "reason": None},
        {"flagged": False, "suggestion": None, "kind": None, "reason": None},
        {"flagged": False, "suggestion": None, "kind": None, "reason": None},
    ]


def test_translate_lines_no_spell_field_marks_unavailable(monkeypatch):
    # N8N workflow not yet updated to return "spell" — must be
    # distinguishable from "spell ran and found nothing".
    from artwork_check import translate

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"translations": ["a"]}

    monkeypatch.setattr(translate.requests, "post",
                        lambda *a, **k: FakeResp())
    monkeypatch.setattr(translate.config, "N8N_TRANSLATE_WEBHOOK_URL",
                        "http://x/webhook/artwork-translate")
    out = translate.translate_lines(["1"])
    assert out["spell_available"] is False
    assert out["spell"] == [{"flagged": False, "suggestion": None,
                             "kind": None, "reason": None}]


def test_translate_lines_reason_and_kind(monkeypatch):
    # New advisory fields: "reason" (short Thai explanation) + "kind"
    # ("typo"/"truncated"/"variant"). Unknown kinds and blank reasons
    # must degrade to None, never break the entry.
    from artwork_check import translate

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {
                "translations": ["Dietary fibre", "Ingredi", "x"],
                "spell": [
                    {"flagged": True, "suggestion": "Dietary fiber",
                     "kind": "variant",
                     "reason": "fibre เป็นการสะกดแบบ British English"},
                    {"flagged": True, "suggestion": "Ingredients",
                     "kind": "banana", "reason": "   "},
                    {"flagged": False, "suggestion": None},
                ],
            }

    monkeypatch.setattr(translate.requests, "post",
                        lambda *a, **k: FakeResp())
    monkeypatch.setattr(translate.config, "N8N_TRANSLATE_WEBHOOK_URL",
                        "http://x/webhook/artwork-translate")
    out = translate.translate_lines(["Dietary fibre", "Ingredi", "x"])
    assert out["spell_available"] is True
    assert out["spell"][0] == {
        "flagged": True, "suggestion": "Dietary fiber",
        "kind": "variant", "reason": "fibre เป็นการสะกดแบบ British English"}
    # invalid kind + whitespace-only reason -> None, entry still usable
    assert out["spell"][1] == {"flagged": True, "suggestion": "Ingredients",
                               "kind": None, "reason": None}
    assert out["spell"][2] == {"flagged": False, "suggestion": None,
                               "kind": None, "reason": None}


def test_translate_lines_network_failure_returns_empty(monkeypatch):
    from artwork_check import translate

    def boom(*a, **k):
        raise translate.requests.RequestException("down")
    monkeypatch.setattr(translate.requests, "post", boom)
    monkeypatch.setattr(translate.config, "N8N_TRANSLATE_WEBHOOK_URL",
                        "http://x/webhook/artwork-translate")
    assert translate.translate_lines(["a"]) == {
        "translations": [], "spell": [], "spell_available": False}


def test_translate_cache_round_trip(tmp_path, monkeypatch):
    from artwork_check import translate
    rows = [{"src": "hola", "status": "ok", "flagged": [], "suggest": {}},
            {"src": "16785", "status": "ok", "flagged": [], "suggest": {}}]
    monkeypatch.setattr(translate.config, "N8N_TRANSLATE_WEBHOOK_URL",
                        "http://x/webhook/artwork-translate")
    monkeypatch.setattr(translate, "translate_lines",
                        lambda lines, **k: {
                            "translations": ["hello", "16785"],
                            "spell": [{"flagged": False, "suggestion": None},
                                     {"flagged": False, "suggestion": None}],
                            "spell_available": True,
                        })
    r1 = translate.translate_table(str(tmp_path), [dict(x) for x in rows])
    assert r1["translated"] and r1["rows"][0]["en"] == "hello"
    assert r1["rows"][0]["ai_spell"] == {"flagged": False, "suggestion": None}
    assert r1["ai_spell_available"] is True

    # second call must hit cache, not the (now exploding) translator
    monkeypatch.setattr(translate, "translate_lines",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("cache miss")))
    r2 = translate.translate_table(str(tmp_path), [dict(x) for x in rows])
    assert r2.get("cached") is True and r2["rows"][1]["en"] == "16785"
    assert r2["ai_spell_available"] is True


def test_translate_table_no_webhook_is_advisory(tmp_path, monkeypatch):
    from artwork_check import translate
    monkeypatch.setattr(translate.config, "N8N_TRANSLATE_WEBHOOK_URL", "")
    rows = [{"src": "x", "status": "ok", "flagged": [], "suggest": {}}]
    res = translate.translate_table(str(tmp_path), rows)
    assert res["translated"] is False
    assert res["ai_spell_available"] is False
    assert all("en" in r and "ai_spell" in r for r in res["rows"])  # usable


# ── OCR-only path (translate tab WITHOUT a full inspection) ────────────

def test_run_ocr_only_caches_and_stays_isolated(tmp_path, monkeypatch):
    """run_ocr_only must OCR on demand, reuse the cache when zones are
    unchanged, re-OCR when they change, and NEVER write a report.json
    (so it can't create/affect a verdict)."""
    import os
    from artwork_check import pipeline, report

    rec_id = "20260101-000000-abcdef"
    monkeypatch.setattr(report.config, "INSPECTIONS_DIR", str(tmp_path))
    d = report.inspection_dir(rec_id, create=True)
    with open(os.path.join(d, "source.png"), "wb") as f:
        f.write(b"not-a-real-image")   # only needs to exist for _find_source

    # Avoid real PDF/image decoding and real OCR network calls.
    monkeypatch.setattr(pipeline, "ArtworkDocument", lambda *a, **k: object())
    calls = {"n": 0}

    def fake_read(doc, zones):
        calls["n"] += 1
        return [{"zone_id": z["id"], "text": "Hello", "engine": "stub",
                 "conf": None} for z in zones]

    monkeypatch.setattr(pipeline.ocr, "read_all_zones", fake_read)

    zones = [{"id": "z1", "type": "panel", "group": "",
              "bbox": [0.1, 0.1, 0.2, 0.2]}]
    _, ocr1 = pipeline.run_ocr_only(rec_id, zones)
    assert calls["n"] == 1 and ocr1[0]["text"] == "Hello"

    # Same zones → cache hit, no second OCR.
    pipeline.run_ocr_only(rec_id, zones)
    assert calls["n"] == 1

    # Different zone layout → cache invalidated, re-OCR.
    zones2 = [dict(zones[0], bbox=[0.3, 0.3, 0.2, 0.2])]
    pipeline.run_ocr_only(rec_id, zones2)
    assert calls["n"] == 2

    # Isolation: no verdict artifact was ever written.
    assert report.load_report(rec_id) is None
    assert not os.path.exists(os.path.join(d, "overlay.png"))


def test_run_ocr_only_missing_upload_raises(tmp_path, monkeypatch):
    from artwork_check import pipeline, report
    monkeypatch.setattr(report.config, "INSPECTIONS_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        pipeline.run_ocr_only("20260101-000000-abcdef",
                              [{"id": "z1", "type": "panel", "group": "",
                                "bbox": [0.1, 0.1, 0.2, 0.2]}])
