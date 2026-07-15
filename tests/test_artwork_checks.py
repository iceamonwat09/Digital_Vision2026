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


# ── Cross-file compare (doc "a" = ไฟล์หลัก, doc "b" = ไฟล์อ้างอิง) ─────

def test_sanitize_zones_doc_field_defaults_and_validates():
    from artwork_check.zones import sanitize_zones

    # payload เก่า (ไม่มี doc) → "a" เสมอ = พฤติกรรมไฟล์เดียวเดิม
    out = sanitize_zones([{"id": "z1", "type": "panel", "group": "",
                           "bbox": [0.1, 0.1, 0.2, 0.2]}])
    assert out[0]["doc"] == "a"

    out = sanitize_zones([{"id": "b1", "type": "panel", "group": "G",
                           "bbox": [0.1, 0.1, 0.2, 0.2], "doc": "B"}])
    assert out[0]["doc"] == "b"          # normalize เป็นตัวเล็ก

    with pytest.raises(ValueError):
        sanitize_zones([{"id": "z1", "type": "panel", "group": "",
                         "bbox": [0.1, 0.1, 0.2, 0.2], "doc": "c"}])


def test_cross_file_group_voting_ignores_doc():
    """ชั้นตรวจไม่รู้จักไฟล์ — โซนต่างไฟล์ที่ group เดียวกันถูกเทียบ
    เหมือน panel ในไฟล์เดียวทุกอย่าง (นี่คือหัวใจของ cross-file compare)."""
    zones = [dict(_zone("z1"), doc="a"), dict(_zone("z2"), doc="a"),
             dict(_zone("b1"), doc="b")]
    texts = {"z1": PANEL_OK, "z2": PANEL_OK, "b1": PANEL_TYPO}
    defects = check_group_consistency(zones, texts)
    assert any(d["class"] == "MISMATCH_PANELS" and d["zone_id"] == "b1"
               for d in defects)


def test_cross_file_zoom_reference_attributes_to_primary():
    """โซนฝั่งไฟล์อ้างอิง (ฉบับเก่า) ตั้ง type=zoom → เป็น "ต้นแบบอ้างอิง"
    ความผิดถูกชี้ไปที่โซนไฟล์หลัก (ของที่กำลังจะพิมพ์) พร้อม reference."""
    zones = [dict(_zone("z1"), doc="a"),
             dict(_zone("b1", ztype="zoom"), doc="b")]
    texts = {"z1": "¡Para mejor caliddd", "b1": "¡Para mejor calidad!"}
    defects = check_group_consistency(zones, texts)
    hits = [d for d in defects if d["class"] == "MISMATCH_ZOOM"]
    assert hits and hits[0]["zone_id"] == "z1"
    assert "calidad" in hits[0]["reference"]


def test_read_all_docs_routes_each_zone_to_its_own_file(tmp_path, monkeypatch):
    import os
    from artwork_check import pipeline, report

    rec_id = "20260101-000000-abc123"
    monkeypatch.setattr(report.config, "INSPECTIONS_DIR", str(tmp_path))
    d = report.inspection_dir(rec_id, create=True)
    for base in ("source.png", "source_b.png"):
        with open(os.path.join(d, base), "wb") as f:
            f.write(b"x")

    opened = []
    monkeypatch.setattr(pipeline, "ArtworkDocument",
                        lambda path, *a, **k: opened.append(
                            os.path.basename(path)) or object())
    monkeypatch.setattr(
        pipeline.ocr, "read_all_zones",
        lambda doc, zones: [{"zone_id": z["id"], "text": "T",
                             "engine": "stub", "conf": None} for z in zones])

    za = [{"id": "z1", "type": "panel", "group": "G",
           "bbox": [0.1, 0.1, 0.2, 0.2], "doc": "a"}]
    zb = [{"id": "b1", "type": "panel", "group": "G",
           "bbox": [0.1, 0.1, 0.2, 0.2], "doc": "b"}]
    results = pipeline._read_all_docs(d, za, zb)
    assert [r["zone_id"] for r in results] == ["z1", "b1"]
    assert opened == ["source.png", "source_b.png"]

    # ไม่มีโซนฝั่ง b → เปิดไฟล์หลักไฟล์เดียว = เส้นทางไฟล์เดียวเดิมเป๊ะ
    opened.clear()
    pipeline._read_all_docs(d, za, [])
    assert opened == ["source.png"]


def test_read_all_docs_missing_ref_file_raises(tmp_path, monkeypatch):
    import os
    from artwork_check import pipeline, report

    rec_id = "20260101-000000-abc456"
    monkeypatch.setattr(report.config, "INSPECTIONS_DIR", str(tmp_path))
    d = report.inspection_dir(rec_id, create=True)
    with open(os.path.join(d, "source.png"), "wb") as f:
        f.write(b"x")
    monkeypatch.setattr(pipeline, "ArtworkDocument", lambda *a, **k: object())
    monkeypatch.setattr(pipeline.ocr, "read_all_zones", lambda doc, zones: [])

    zb = [{"id": "b1", "type": "panel", "group": "G",
           "bbox": [0.1, 0.1, 0.2, 0.2], "doc": "b"}]
    with pytest.raises(ValueError):
        pipeline._read_all_docs(d, [], zb)


def test_zones_signature_includes_doc():
    """เปลี่ยนไฟล์ของโซน (doc) ต้องทำให้แคช OCR-only ของแท็บแปล invalidate."""
    from artwork_check import pipeline
    za = [{"id": "z1", "type": "panel", "group": "",
           "bbox": [0.1, 0.1, 0.2, 0.2], "doc": "a"}]
    zb = [dict(za[0], doc="b")]
    assert pipeline._zones_signature(za) != pipeline._zones_signature(zb)


# ── OCR line-segmentation noise (เคสจริง: รูปเดียวกันอัปโหลด 2 ครั้ง) ──

# ข้อความ OCR จริงจากสถานี — ภาพเดียวกัน แต่รอบแรกอ่านแยกคอลัมน์
# (label กับค่าคนละบรรทัด, ไม่ติดกัน) รอบสองอ่านรวมเป็นแถวเดียว
_GA_SPLIT = """營養標示分析
Guaranteed Analysis
每100
公克(Per 100g)
粗蛋白質 Cude Protein
粗脂肪 Cude Fat
8.0%以上 Min
0.2%以上 Min
灰分 Ash
2.0%以下 Max
粗纖維 Cude Fiber
1.0%以下
Max
水分 Moisture
90.0%以下
Max
磷 Phosphours
0.040%以上 Min
3.08
熱量 Energy
kcal/7公克(g)"""

_GA_MERGED = """營養標示分析
Guaranteed Analysis
每100公克(Per 100g)
粗蛋白質 Cude Protein 8.0%以上 Min
粗脂肪 Cude Fat
0.2%以上 Min
灰分 Ash
2.0%以下 Max
粗纖維 Cude Fiber
1.0%以下 Max
水分 Moisture
90.0%以下 Max
磷 Phosphours
0.040%以上 Min
熱量 Energy
3.08 kcal/7公克(g)"""


def test_voting_forgives_ocr_column_merge_noise():
    """รูปเดียวกันสองไฟล์ — บรรทัดที่ OCR merge มาจากคนละตำแหน่ง
    (ไม่ติดกัน) ของอีกฝั่ง ต้องไม่ถูกฟ้องเป็น MISMATCH_PANELS."""
    zones = [dict(_zone("z1"), doc="a"), dict(_zone("b2"), doc="b")]
    texts = {"z1": _GA_SPLIT, "b2": _GA_MERGED}
    defects = check_group_consistency(zones, texts)
    assert [d for d in defects if d["class"] == "MISMATCH_PANELS"] == []


def test_voting_still_flags_typo_despite_merge_forgiveness():
    """การ forgive แบบต่อทั้งบรรทัดต้องไม่กลบ typo จริง."""
    typo = _GA_MERGED.replace("Guaranteed", "Guarenteed")
    zones = [_zone("z1"), _zone("z2")]
    texts = {"z1": _GA_SPLIT, "z2": typo}
    defects = check_group_consistency(zones, texts)
    assert any(d["class"] == "MISMATCH_PANELS" and
               "Guarenteed" in (d["found"] + d["reference"])
               for d in defects)


def test_voting_still_flags_swapped_values():
    """ค่าที่สลับแถวกัน (8.0 ↔ 0.2) ประกอบจาก "ทั้งบรรทัด" ของอีกฝั่ง
    ไม่ได้ → ต้องถูกฟ้อง ไม่ถูก forgive."""
    zones = [_zone("z1"), _zone("z2")]
    texts = {
        "z1": "Protein 8.0% Min\nFat 0.2% Min",
        "z2": "Protein 0.2% Min\nFat 8.0% Min",
    }
    defects = check_group_consistency(zones, texts)
    assert any(d["class"] == "MISMATCH_PANELS" for d in defects)


def test_composable_from_basics():
    from artwork_check.checks import _composable_from
    # merge ของสองบรรทัดเต็ม → ประกอบได้
    assert _composable_from("AABBB", ["AA", "BBB", "CC"])
    # ชิ้นส่วนไม่ตรงทั้งบรรทัด → ไม่ได้
    assert not _composable_from("AABX", ["AA", "BBB"])
    assert not _composable_from("", ["AA"])
    assert not _composable_from("AA", [])


# ── OCR แตกหัวตาราง CJK เป็นตัวอักษรละบรรทัด (เคสจริง: 品 名) ─────────

def test_voting_forgives_cjk_header_split_per_char():
    """b3 อ่าน "品 名" เป็น 2 บรรทัด ตัวละตัว (แถมสลับลำดับ: 名, 品)
    ขณะที่ z4 อ่านรวมเป็น "品名" — ต้องไม่ฟ้อง MISMATCH_PANELS."""
    zones = [dict(_zone("z4"), doc="a"), dict(_zone("b3"), doc="b")]
    texts = {
        "z4": "品名\nPuffy帕菲Nee泥泥愛貓肉泥條(雞肉+牛磺酸)\n淨重\n70公克(7公克×10包)",
        "b3": "名\n品\nPuffy帕菲Nee泥泥愛貓肉泥條(雞肉+牛磺酸)\n淨重\n70公克(7公克×10包)",
    }
    defects = check_group_consistency(zones, texts)
    assert [d for d in defects if d["class"] == "MISMATCH_PANELS"] == []


def test_voting_still_flags_cjk_char_order_swap():
    """ตัวอักษรจีนสลับลำดับจริงในบรรทัดเดียว (品名 vs 名品 โดยไม่ได้แตก
    เป็นตัวละบรรทัด) = ความต่างจริง ต้องถูกฟ้อง."""
    zones = [_zone("z1"), _zone("z2")]
    texts = {"z1": "品名\n淨重70公克", "z2": "名品\n淨重70公克"}
    defects = check_group_consistency(zones, texts)
    assert any(d["class"] == "MISMATCH_PANELS" for d in defects)


# ── OCR ฉีกเศษท้ายแถวไปแปะบรรทัดอื่น (เคสจริง: ภาพใหญ่ทั้งฉลาก) ───────

def test_voting_forgives_row_tail_glued_to_other_row():
    """b2 อ่านค่าของแถว Fat (0.2%以上 Min) ไปต่อท้ายแถว Protein
    ทำให้ทั้งสองฝั่งมีบรรทัดที่หาแบบ "ทั้งบรรทัด" ในอีกฝั่งไม่เจอ —
    เศษ ≥6 ตัวอักษร 1 ชิ้นต้องถูกยกโทษ."""
    zones = [dict(_zone("z1"), doc="a"), dict(_zone("b2"), doc="b")]
    texts = {
        "z1": ("營養標示分析\n粗蛋白質 Cude Protein 8.0%以上 Min\n"
               "粗脂肪 Cude Fat 0.2%以上 Min\n灰分 Ash 2.0%以下 Max"),
        "b2": ("營養標示分析\n粗蛋白質 Cude Protein 8.0%以上 Min 0.2%以上 Min\n"
               "粗脂肪 Cude Fat\n灰分 Ash 2.0%以下 Max"),
    }
    defects = check_group_consistency(zones, texts)
    assert [d for d in defects if d["class"] == "MISMATCH_PANELS"] == []


def test_composable_fragment_rules():
    from artwork_check.checks import _composable_from
    lines = ["ABCDEFGH", "XYZ12345"]
    # ทั้งบรรทัด + เศษยาว 1 ชิ้น → ได้
    assert _composable_from("ABCDEFGHXYZ123", lines)          # whole + frag(6)
    # เศษสั้นกว่า 6 → ไม่ได้
    assert not _composable_from("ABCDEFGHXYZ", lines)         # frag len 3
    # ต้องใช้เศษ 2 ชิ้น → ไม่ได้ (กัน typo/ค่าสลับแถวถูกกลบ)
    assert not _composable_from("ABCDEFXYZ123", lines)        # frag(6)+frag(6)
    # อักษรจีนตัวเดียวเป็นชิ้นได้ / ละตินตัวเดียวไม่ได้
    assert _composable_from("品名", ["品", "名"])
    assert not _composable_from("AB", ["A", "B"])


# ── Arabic orthography normalization (เคสจริง: ฉลาก John West อาหรับ) ──

_AR_BASE = """المكونات: تونا، زيت دوار الشمس، ماء، ملح.
شروط التخزين: يحفظ في مكان جاف بارد وجيد التهوية.
تاريخ الإنتاج والانتهاء: انظر العبوة.
الوزن الصافي: 170 جم الوزن المصفى: 120 جم
Ingredients: Tuna, Sunflower Oil, Water, Salt.
Produced in Thailand
NET WEIGHT 170g DRAINED WEIGHT 120g"""


def _cross_zones():
    return [dict(_zone("z1"), doc="a"), dict(_zone("b1"), doc="b")]


def _mm(texts):
    return [d for d in check_group_consistency(_cross_zones(), texts)
            if d["class"].startswith("MISMATCH")]


def test_arabic_hamza_variance_forgiven():
    """รูปเดียวกัน OCR ได้ انظر/أنظر (ا vs أ) — transcription noise."""
    assert _mm({"z1": _AR_BASE,
                "b1": _AR_BASE.replace("انظر", "أنظر")}) == []


def test_arabic_maqsura_variance_forgiven():
    """ى/ي — ฉลากจริงยังสะกดปนกันเองบนใบเดียว (الصافي/الصافى)."""
    assert _mm({"z1": _AR_BASE,
                "b1": _AR_BASE.replace("الصافي", "الصافى")}) == []


def test_arabic_harakat_variance_forgiven():
    assert _mm({"z1": _AR_BASE,
                "b1": _AR_BASE.replace("تونا", "تُونا")}) == []


def test_fullwidth_variance_forgiven():
    assert _mm({"z1": _AR_BASE,
                "b1": _AR_BASE.replace("NET WEIGHT 170g",
                                       "ＮＥＴ ＷＥＩＧＨＴ １７０ｇ")}) == []


def test_arabic_real_letter_typo_still_flagged():
    """خ→ح คือตัวอักษรคนละตัวจริง — normalize ต้องไม่กลบ."""
    assert _mm({"z1": _AR_BASE,
                "b1": _AR_BASE.replace("التخزين", "التحزين")})


def test_arabic_real_word_missing_still_flagged():
    assert _mm({"z1": _AR_BASE,
                "b1": _AR_BASE.replace("ماء، ملح", "ملح")})


def test_real_number_diff_still_flagged():
    assert _mm({"z1": _AR_BASE,
                "b1": _AR_BASE.replace("170 جم", "190 جم")})


def test_latin_accent_still_meaningful():
    """é ≠ e มีความหมายจริงบนฉลากสเปน/ฝรั่งเศส — ต้องไม่ถูก normalize ทิ้ง."""
    from artwork_check.checks import _norm_key
    assert _norm_key("mejoré") != _norm_key("mejore")


# ── Barcode: EAN-13 พิมพ์หลักแรกแยกโคนบาร์โค้ด (เคสจริง: ฉลาก AYAM) ──

def _barcode_flags(text):
    z = [{"id": "z1", "type": "panel", "group": "",
          "bbox": [0.1, 0.1, 0.2, 0.2]}]
    return [d for d in check_numbers(z, {"z1": text})
            if "บาร์โค้ด" in d["message"]]


def test_barcode_split_leading_digit_not_flagged():
    """OCR อ่าน "9" แยกจาก 12 หลักที่เหลือ — 13 หลักรวมกัน valid
    ต้องไม่ฟ้อง (เดิมตีความ 12 หลักเป็น UPC-A แล้ว FAIL ปลอม)."""
    assert _barcode_flags("9\n556041641272\nGC (SG) A / 003") == []


def test_barcode_split_with_real_bad_digit_still_flagged():
    assert _barcode_flags("9\n556041641273")


def test_barcode_full_bad_check_digit_still_flagged():
    assert _barcode_flags("9556041641279")


def test_barcode_full_valid_not_flagged():
    assert _barcode_flags("9556041641272") == []


def test_phone_number_run_not_treated_as_barcode():
    assert _barcode_flags("toll free line: 1800 45 45 457") == []


# ── Phase 2: จับคู่ความต่างข้ามไฟล์เป็น defect เดียว (พบ/เทียบกับ) ────

def test_cross_doc_pair_merges_to_single_defect():
    a = "HIDDEN BAY\nSKIPJAK TUNA\nPACKED IN WATER"    # ไฟล์หลัก (typo)
    b = "HIDDEN BAY\nSKIPJACK TUNA\nPACKED IN WATER"   # ไฟล์อ้างอิง
    mm = _mm({"z1": a, "b1": b})
    assert len(mm) == 1
    assert mm[0]["zone_id"] == "z1"                     # ชี้ไฟล์หลัก
    assert mm[0]["found"] == "SKIPJAK TUNA"
    assert mm[0]["reference"] == "SKIPJACK TUNA"
    assert mm[0]["ref_zone_ids"] == ["b1"]


def test_same_doc_two_panel_behavior_unchanged():
    """กลุ่ม 2 panel ภายในไฟล์เดียว — ยังฟ้องแยก 2 ใบตามพฤติกรรมเดิม."""
    zones = [_zone("z1"), _zone("z2")]
    texts = {"z1": "HIDDEN BAY\nSKIPJAK TUNA",
             "z2": "HIDDEN BAY\nSKIPJACK TUNA"}
    mm = [d for d in check_group_consistency(zones, texts)
          if d["class"] == "MISMATCH_PANELS"]
    assert len(mm) == 2


def test_cross_doc_unrelated_lines_not_paired():
    mm = _mm({"z1": "HIDDEN BAY\nTOTALLY DIFFERENT LINE",
              "b1": "HIDDEN BAY\nANOTHER THING HERE XYZ"})
    assert len(mm) == 2


# ── Phase 3: spell stoplist ────────────────────────────────────────────

@pytest.mark.skipif(not checks.spell_layer_available(),
                    reason="pyspellchecker not installed")
def test_spell_skips_url_tokens():
    zones = [_zone("z1", group="")]
    texts = {"z1": "Visit https://www.example.com for details"}
    words = [d["found"] for d in checks.check_spelling(zones, texts)]
    assert "https" not in words and "www" not in words


# ── ตัวอักษรเว้นช่อง O P E N (ภาพจริง: OPEN & EAT) ────────────────────

def test_letter_spaced_caps_split_lines_forgiven():
    merged = "OPEN & EAT\non SANDWICH, TACO or WRAP"
    split = "O\nP\nE\nN\n&\nE\nA\nT\non SANDWICH, TACO or WRAP"
    assert _mm({"z1": merged, "b1": split}) == []
