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
    # spell array ที่ยาวไม่ตรงจำนวนบรรทัด = ตำแหน่งเชื่อไม่ได้ทั้งก้อน
    # (โมเดลอาจข้ามบรรทัดกลางลิสต์) → ทุกบรรทัดต้องถูกตีตรา "ตรวจไม่ครบ"
    # ห้ามเติมท้ายด้วย flagged:False ปลอมๆ ที่จะโชว์เป็น "✓ ไม่พบ"
    assert all(sp.get("missing") is True and sp["flagged"] is False
               for sp in out["spell"])
    assert len(out["spell"]) == 3


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

    def fake_read(doc, zones, page_auto=False):
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
        lambda doc, zones, page_auto=False: [{"zone_id": z["id"], "text": "T",
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
    monkeypatch.setattr(pipeline.ocr, "read_all_zones",
                        lambda doc, zones, page_auto=False: [])

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


# ── Auto sequential groups (จับคู่ข้ามไฟล์อัตโนมัติตามลำดับ A B C…) ────

def test_seq_group_sequence():
    from artwork_check.zones import seq_group, GROUP_LETTERS
    assert [seq_group(i) for i in range(4)] == ["A", "B", "C", "D"]
    assert "I" not in GROUP_LETTERS and "O" not in GROUP_LETTERS
    n = len(GROUP_LETTERS)
    assert seq_group(n - 1) == "Z"
    assert seq_group(n) == "A2"          # รอบสอง
    assert seq_group(2 * n) == "A3"


def test_propose_zones_sequential_groups():
    """เสนอโซน → group ไล่ A,B,C.. ตามลำดับอ่าน ไม่ซ้ำ ไม่ถูกล้าง
    แม้สองบล็อกจะขนาดเท่ากันเป๊ะ (heuristic ขนาดถูกถอดแล้ว)."""
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    from artwork_check.zones import propose_zones, seq_group
    img = np.full((800, 1200, 3), 255, np.uint8)
    img[60:180, 80:520] = 0       # ขนาดเท่ากับบล็อกถัดไปเป๊ะ
    img[60:180, 640:1080] = 0
    img[380:560, 80:700] = 0
    img[640:750, 80:400] = 0
    zones = propose_zones(img)
    assert len(zones) >= 3
    assert [z["group"] for z in zones] == \
        [seq_group(i) for i in range(len(zones))]
    assert len({z["group"] for z in zones}) == len(zones)


def test_start_ref_groups_align_with_primary(tmp_path, monkeypatch):
    """แนบไฟล์ชิ้นงาน → b1..bN ได้ group ลำดับเดียวกับ z1..zN
    (ไม่มี prefix 'b' อีกแล้ว) เพื่อจับคู่เทียบข้ามไฟล์อัตโนมัติ."""
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    import os
    from artwork_check import pipeline, report
    monkeypatch.setattr(report.config, "INSPECTIONS_DIR", str(tmp_path))

    img = np.full((600, 900, 3), 255, np.uint8)
    img[50:150, 60:420] = 0
    img[250:400, 60:500] = 0
    img[460:560, 60:300] = 0
    ok, buf = cv2.imencode(".png", img)
    assert ok
    res_a = pipeline.start_inspection(buf.tobytes(), "new.png")
    res_b = pipeline.start_ref(res_a["id"], buf.tobytes(), "ref.png")

    ga = [z["group"] for z in res_a["zones"]]
    gb = [z["group"] for z in res_b["zones"]]
    assert ga and ga == gb                       # ลำดับตรงกัน → จับคู่เอง
    assert all(z["doc"] == "b" for z in res_b["zones"])
    assert not any(z["group"].startswith("b") for z in res_b["zones"])


def test_single_member_auto_groups_produce_no_defects():
    """ช่องโหว่ที่ต้องกัน: โซนไฟล์เดียวได้ group เดี่ยว A,B,C.. ทุกโซน —
    ห้ามมี defect โผล่จาก group ที่มีสมาชิกเดียว (voting ต้อง ≥2 panel)."""
    zones = [dict(_zone("z1", group="A")), dict(_zone("z2", group="B")),
             dict(_zone("z3", group="C"))]
    texts = {"z1": "AAA BBB", "z2": "CCC DDD", "z3": "EEE FFF"}
    assert check_group_consistency(zones, texts) == []


def test_unequal_zone_counts_extra_group_is_inert():
    """ไฟล์หลัก 3 โซน / ชิ้นงาน 2 โซน → group C เหลือเดี่ยว ต้องเงียบ
    ส่วนคู่ A,B ที่ครบยังเทียบกันปกติ."""
    zones = [dict(_zone("z1", group="A"), doc="a"),
             dict(_zone("z2", group="B"), doc="a"),
             dict(_zone("z3", group="C"), doc="a"),
             dict(_zone("b1", group="A"), doc="b"),
             dict(_zone("b2", group="B"), doc="b")]
    texts = {"z1": "HELLO WORLD", "z2": "NET WEIGHT 170g",
             "z3": "ONLY IN PRIMARY", "b1": "HELLO WORLD",
             "b2": "NET WEIGHT 190g"}          # B ต่างจริง
    defects = [d for d in check_group_consistency(zones, texts)
               if d["class"] == "MISMATCH_PANELS"]
    # กลุ่ม C (เดี่ยว) ต้องไม่ฟ้อง; กลุ่ม B ฟ้องคู่เดียวแบบ found/reference
    assert all(d["zone_id"] != "z3" for d in defects)
    assert len(defects) == 1 and defects[0]["zone_id"] == "z2"
    assert "170" in defects[0]["found"] and "190" in defects[0]["reference"]


# ── On-demand proposal (ปุ่ม "เสนอโซนใหม่" — ไม่เสนออัตโนมัติแล้ว) ────

def test_propose_for_both_docs(tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    from artwork_check import pipeline, report
    monkeypatch.setattr(report.config, "INSPECTIONS_DIR", str(tmp_path))

    img = np.full((600, 900, 3), 255, np.uint8)
    img[50:150, 60:420] = 0
    img[250:400, 60:500] = 0
    ok, buf = cv2.imencode(".png", img)
    res_a = pipeline.start_inspection(buf.tobytes(), "new.png")

    # doc a: id z*, ไม่มี doc field b
    za = pipeline.propose_for(res_a["id"], "a")
    assert za and all(z["id"].startswith("z") for z in za)

    # doc b ก่อนแนบไฟล์ → FileNotFoundError ข้อความชัด
    with pytest.raises(FileNotFoundError):
        pipeline.propose_for(res_a["id"], "b")

    pipeline.start_ref(res_a["id"], buf.tobytes(), "ref.png")
    zb = pipeline.propose_for(res_a["id"], "b")
    assert zb and all(z["doc"] == "b" and z["id"].startswith("b")
                      for z in zb)
    # ลำดับ group ตรงกับฝั่ง a (ภาพเดียวกัน) → จับคู่อัตโนมัติ
    assert [z["group"] for z in za] == [z["group"] for z in zb]

    with pytest.raises(ValueError):
        pipeline.propose_for(res_a["id"], "x")


# ── Chunked translation (แบ่งก้อนละ 30 บรรทัด — แก้ AI เพี้ยนตอนโซนเยอะ) ─

def _fake_ok_chunk(lines):
    """คำตอบสมบูรณ์ 1 ก้อน: แปล = "EN:"+src, spell align ครบ."""
    return {"translations": ["EN:" + l for l in lines],
            "spell": [{"flagged": False, "suggestion": None,
                       "kind": None, "reason": None} for _ in lines],
            "spell_available": True}


def test_chunked_splits_and_merges_in_order(monkeypatch):
    from artwork_check import translate
    calls = []
    monkeypatch.setattr(translate, "translate_lines",
                        lambda lines, check_words=None:
                        calls.append(len(lines)) or _fake_ok_chunk(lines))
    lines = [f"L{i}" for i in range(70)]
    out = translate.translate_lines_chunked(lines, chunk_size=30)
    assert calls == [30, 30, 10]
    assert out["translations"] == ["EN:" + l for l in lines]   # ลำดับคงเดิม
    assert out["chunks_total"] == 3 and out["chunks_failed"] == 0
    assert out["spell_available"] is True


def test_chunked_zero_is_single_request_rollback(monkeypatch):
    """ARTWORK_TRANSLATE_CHUNK_LINES=0 = ส่งก้อนเดียวแบบเดิม (ปุ่มถอยกลับ)."""
    from artwork_check import translate
    calls = []
    monkeypatch.setattr(translate, "translate_lines",
                        lambda lines, check_words=None:
                        calls.append(len(lines)) or _fake_ok_chunk(lines))
    translate.translate_lines_chunked([f"L{i}" for i in range(70)],
                                      chunk_size=0)
    assert calls == [70]


def test_chunked_failed_chunk_marks_missing_not_clean(monkeypatch):
    """ก้อนที่ล้ม: แปลว่าง + spell = missing (ห้ามเป็น "ไม่พบ" ปลอม)
    ก้อนอื่นต้องไม่ติดเชื้อ."""
    from artwork_check import translate
    n = {"i": 0}

    def fake(lines, check_words=None):
        n["i"] += 1
        if n["i"] == 2:      # ก้อนกลางล้ม
            return {"translations": [], "spell": [],
                    "spell_available": False}
        return _fake_ok_chunk(lines)

    monkeypatch.setattr(translate, "translate_lines", fake)
    lines = [f"L{i}" for i in range(70)]
    out = translate.translate_lines_chunked(lines, chunk_size=30)
    assert out["chunks_failed"] == 1 and out["chunks_total"] == 3
    assert out["translations"][0] == "EN:L0"
    assert out["translations"][30] == "" and out["translations"][59] == ""
    assert out["translations"][60] == "EN:L60"
    assert out["spell"][30].get("missing") is True
    assert out["spell"][0].get("missing") is None
    assert out["spell_available"] is True   # ก้อนที่สำเร็จมี spell ครบ


def test_translate_table_partial_failure_not_cached(tmp_path, monkeypatch):
    """ล้มบางก้อน → แสดงส่วนที่ได้ + note บอกตรง + ห้ามเขียนแคช
    (กันบรรทัดว่างค้างถาวร — กดแปลซ้ำแล้วเติมได้)."""
    import os
    from artwork_check import translate
    monkeypatch.setattr(translate.config, "N8N_TRANSLATE_WEBHOOK_URL",
                        "http://x/webhook/artwork-translate")
    monkeypatch.setattr(
        translate, "translate_lines_chunked",
        lambda lines, check_words=None: {
            "translations": ["EN:" + l for l in lines[:1]] +
                            [""] * (len(lines) - 1),
            "spell": [translate._clean_spell()] +
                     [translate._missing_spell()] * (len(lines) - 1),
            "spell_available": True,
            "chunks_total": 2, "chunks_failed": 1,
        })
    rows = [{"src": f"S{i}", "status": "ok", "flagged": [], "suggest": {}}
            for i in range(3)]
    res = translate.translate_table(str(tmp_path), rows)
    assert res["translated"] is True
    assert "1/2" in res["note"] or "แปลสำเร็จ 1/2" in res["note"]
    assert not os.path.exists(os.path.join(str(tmp_path),
                                           "translation.json"))
    assert res["rows"][1]["ai_spell"].get("missing") is True


def test_translate_table_complete_result_cached(tmp_path, monkeypatch):
    """ครบทุกก้อน + align ครบ → เขียนแคชตามเดิม."""
    import os
    from artwork_check import translate
    monkeypatch.setattr(translate.config, "N8N_TRANSLATE_WEBHOOK_URL",
                        "http://x/webhook/artwork-translate")
    monkeypatch.setattr(
        translate, "translate_lines_chunked",
        lambda lines, check_words=None: {
            "translations": ["EN:" + l for l in lines],
            "spell": [translate._clean_spell() for _ in lines],
            "spell_available": True,
            "chunks_total": 1, "chunks_failed": 0,
        })
    rows = [{"src": "S1", "status": "ok", "flagged": [], "suggest": {}}]
    res = translate.translate_table(str(tmp_path), rows)
    assert res["translated"] is True and "note" not in res
    assert os.path.exists(os.path.join(str(tmp_path), "translation.json"))


def test_translate_table_misaligned_spell_not_cached(tmp_path, monkeypatch):
    """spell เหลื่อม (missing ทั้งก้อน) แม้แปลสำเร็จ → ไม่เขียนแคช
    เพื่อให้กดแปลซ้ำแล้ว AI ได้ตรวจใหม่."""
    import os
    from artwork_check import translate
    monkeypatch.setattr(translate.config, "N8N_TRANSLATE_WEBHOOK_URL",
                        "http://x/webhook/artwork-translate")
    monkeypatch.setattr(
        translate, "translate_lines_chunked",
        lambda lines, check_words=None: {
            "translations": ["EN:" + l for l in lines],
            "spell": [translate._missing_spell() for _ in lines],
            "spell_available": True,
            "chunks_total": 1, "chunks_failed": 0,
        })
    rows = [{"src": "S1", "status": "ok", "flagged": [], "suggest": {}}]
    res = translate.translate_table(str(tmp_path), rows)
    assert res["translated"] is True
    assert not os.path.exists(os.path.join(str(tmp_path),
                                           "translation.json"))


# ── check_words: ส่งคำที่ dict ฟ้องให้ AI ตัดสินรายคำ (แก้ Phosphours หลุด) ─

def test_translate_lines_sends_check_words(monkeypatch):
    from artwork_check import translate
    captured = {}

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"translations": ["a", "b"], "spell": []}

    def fake_post(url, json=None, timeout=None):
        captured["body"] = json
        return FakeResp()

    monkeypatch.setattr(translate.requests, "post", fake_post)
    monkeypatch.setattr(translate.config, "N8N_TRANSLATE_WEBHOOK_URL",
                        "http://x/webhook/artwork-translate")
    translate.translate_lines(["磷 Phosphours", "OK line"],
                              check_words=[["Phosphours"], []])
    assert captured["body"]["lines"] == ["磷 Phosphours", "OK line"]
    assert captured["body"]["check_words"] == [["Phosphours"], []]

    # ไม่มีคำต้องสงสัยเลย → ไม่แนบ field (payload เหมือนก่อนฟีเจอร์นี้ 100%)
    translate.translate_lines(["a"], check_words=[[]])
    assert "check_words" not in captured["body"]
    translate.translate_lines(["a"])
    assert "check_words" not in captured["body"]


def test_chunked_slices_check_words_per_chunk(monkeypatch):
    from artwork_check import translate
    got = []
    monkeypatch.setattr(
        translate, "translate_lines",
        lambda lines, check_words=None: got.append(check_words)
        or _fake_ok_chunk(lines))
    lines = [f"L{i}" for i in range(70)]
    cw = [[f"W{i}"] if i in (0, 35, 69) else [] for i in range(70)]
    translate.translate_lines_chunked(lines, chunk_size=30, check_words=cw)
    assert len(got) == 3
    assert got[0][0] == ["W0"] and got[1][5] == ["W35"] and got[2][9] == ["W69"]
    assert all(len(c) in (30, 10) for c in got)


def test_translate_table_passes_dict_flags_as_check_words(tmp_path,
                                                          monkeypatch):
    from artwork_check import translate
    seen = {}
    monkeypatch.setattr(translate.config, "N8N_TRANSLATE_WEBHOOK_URL",
                        "http://x/webhook/artwork-translate")
    monkeypatch.setattr(
        translate, "translate_lines_chunked",
        lambda lines, check_words=None: seen.update(cw=check_words) or {
            "translations": ["EN"] * len(lines),
            "spell": [translate._clean_spell() for _ in lines],
            "spell_available": True, "chunks_total": 1, "chunks_failed": 0,
        })
    rows = [
        {"src": "磷 Phosphours", "status": "spell",
         "flagged": ["Phosphours"], "suggest": {}},
        {"src": "clean", "status": "ok", "flagged": [], "suggest": {}},
    ]
    translate.translate_table(str(tmp_path), rows)
    assert seen["cw"] == [["Phosphours"], []]


def test_workflow_json_has_adjudication_wiring():
    """workflow ที่ให้ import ต้องมีทั้งการรับ check_words และ prompt
    ตัดสินรายคำ — กัน regression ตอนแก้ workflow ครั้งหน้า."""
    import json as _json
    wf = _json.load(open("artwork_check/n8n_artwork_translate.workflow.json"))
    node = next(n for n in wf["nodes"] if n["name"] == "Code in JavaScript2")
    code = node["parameters"]["jsCode"]
    assert "check_words" in code
    assert "MANDATORY ADJUDICATION LIST" in code
    assert "PROMPT + SUSPECTS" in code


# ── Auto-rotate vertical zones before OCR (แก้ OCR hallucinate แนวตั้ง) ─

def test_detect_orientation_and_apply():
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    from artwork_check.pdf_ingest import (detect_orientation, apply_rotation,
                                          ROTATE_VALUES)
    # ข้อความแนวนอน = แถบกว้างกว่าสูง / แนวตั้ง = สูงกว่ากว้าง
    horiz = np.full((60, 400, 3), 255, np.uint8)
    cv2.putText(horiz, "HELLO WORLD", (10, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3)
    vert = cv2.rotate(horiz, cv2.ROTATE_90_CLOCKWISE)
    assert detect_orientation(horiz) == "horizontal"
    assert detect_orientation(vert) == "vertical"
    assert detect_orientation(np.full((20, 20, 3), 255, np.uint8)) == "empty"
    # apply_rotation ครบ 4 ค่า + คืนขนาดถูก
    assert apply_rotation(horiz, 0).shape == horiz.shape
    assert apply_rotation(horiz, 90).shape == (400, 60, 3)
    assert apply_rotation(horiz, 180).shape == horiz.shape
    assert apply_rotation(horiz, 270).shape == (400, 60, 3)
    assert ROTATE_VALUES == (0, 90, 180, 270)


def test_resolve_rotation_matrix():
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    from artwork_check.pdf_ingest import resolve_rotation
    horiz = np.full((60, 400, 3), 255, np.uint8)
    cv2.putText(horiz, "HELLO WORLD", (10, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3)
    vert = cv2.rotate(horiz, cv2.ROTATE_90_CLOCKWISE)
    assert resolve_rotation(90, False, horiz) == 90            # pinned
    assert resolve_rotation("auto", False, vert) == 270        # vertical→CCW
    assert resolve_rotation("auto", False, horiz) == 0         # horiz→none
    assert resolve_rotation("default", False, vert) == 0       # page OFF = เดิม
    assert resolve_rotation("default", True, vert) == 270      # page ON
    assert resolve_rotation("default", True, horiz) == 0       # page ON แต่แนวนอน
    assert resolve_rotation("bad", True, vert) == 0            # ค่าเพี้ยน = ไม่หมุน


def test_sanitize_rotate_field():
    from artwork_check.zones import sanitize_zones
    def z(rot=None):
        d = {"id": "z1", "type": "panel", "group": "",
             "bbox": [0.1, 0.1, 0.2, 0.2]}
        if rot is not None:
            d["rotate"] = rot
        return d
    assert sanitize_zones([z()])[0]["rotate"] == "default"     # absent → default
    assert sanitize_zones([z("auto")])[0]["rotate"] == "auto"
    assert sanitize_zones([z(90)])[0]["rotate"] == 90
    assert sanitize_zones([z("180")])[0]["rotate"] == 180      # numeric str
    assert sanitize_zones([z(45)])[0]["rotate"] == "default"   # bad int
    assert sanitize_zones([z("xyz")])[0]["rotate"] == "default"


def test_read_zone_applies_rotation(tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    from artwork_check import ocr as ocr_mod
    from artwork_check.pdf_ingest import ArtworkDocument

    # doc ปลอมที่คืน crop แนวตั้ง; เก็บ crop ที่ถูกส่งเข้า OCR ไว้ตรวจ
    horiz = np.full((60, 400, 3), 255, np.uint8)
    cv2.putText(horiz, "HELLO WORLD", (10, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3)
    vert = cv2.rotate(horiz, cv2.ROTATE_90_CLOCKWISE)

    class FakeDoc:
        def embedded_text(self, bbox=None): return ""
        def render_zone(self, *a, **k): return vert

    sent = {}
    monkeypatch.setattr(ocr_mod.vertex_client, "is_enabled", lambda: True)
    def fake_ocr(jpg):
        sent["shape"] = cv2.imdecode(
            np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR).shape
        return {"text": "HELLO WORLD", "engine": "stub", "blocks": []}
    monkeypatch.setattr(ocr_mod.vertex_client, "ocr_image", fake_ocr)

    # page auto ON → โซน default แนวตั้งถูกหมุนกลับเป็นแนวนอนก่อนส่ง OCR
    r = ocr_mod.read_zone(FakeDoc(),
                          {"id": "z1", "bbox": [0, 0, 1, 1], "type": "panel",
                           "rotate": "default"}, page_auto=True)
    assert r["rotate"] == 270
    assert sent["shape"][:2] == horiz.shape[:2]      # กลับเป็นแนวนอน

    # page OFF → ไม่หมุน (พฤติกรรมเดิม)
    r2 = ocr_mod.read_zone(FakeDoc(),
                           {"id": "z1", "bbox": [0, 0, 1, 1], "type": "panel",
                            "rotate": "default"}, page_auto=False)
    assert r2["rotate"] == 0
    assert sent["shape"][:2] == vert.shape[:2]       # ยังตะแคง


def test_run_inspection_writes_applied_rotation(tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    import os
    from artwork_check import pipeline, report
    monkeypatch.setattr(report.config, "INSPECTIONS_DIR", str(tmp_path))
    d = report.inspection_dir("20260101-000000-aa11bb", create=True)
    with open(os.path.join(d, "source.png"), "wb") as f:
        f.write(b"x")
    with open(os.path.join(d, "preview.png"), "wb") as f:
        f.write(cv2.imencode(".png", np.full((50, 80, 3), 255, np.uint8))[1])
    monkeypatch.setattr(pipeline, "ArtworkDocument", lambda *a, **k: object())
    monkeypatch.setattr(
        pipeline.ocr, "read_all_zones",
        lambda doc, zones, page_auto=False: [
            {"zone_id": z["id"], "text": "T", "engine": "stub",
             "conf": None, "rotate": 270 if z["id"] == "z1" else 0}
            for z in zones])
    rep = pipeline.run_inspection(
        "20260101-000000-aa11bb",
        [{"id": "z1", "type": "panel", "group": "", "bbox": [0.1,0.1,0.2,0.2]},
         {"id": "z2", "type": "panel", "group": "", "bbox": [0.3,0.1,0.2,0.2]}],
        auto_rotate=True)
    by = {z["id"]: z["rotate"] for z in rep["zones"]}
    assert by["z1"] == 270 and by["z2"] == 0        # องศาที่ใช้จริงถูกบันทึก
    assert next(o["rotate"] for o in rep["ocr"] if o["zone_id"] == "z1") == 270


def test_ocr_cache_signature_includes_rotate_and_flag():
    from artwork_check import pipeline
    z0 = [{"id": "z1", "type": "panel", "group": "", "bbox": [0.1,0.1,0.2,0.2],
           "doc": "a", "rotate": "default"}]
    z90 = [dict(z0[0], rotate=90)]
    assert pipeline._zones_signature(z0) != pipeline._zones_signature(z90)
    # flag หน้าเปลี่ยน = cache key เปลี่ยน (default resolve ต่างกัน)
    assert (pipeline._zones_signature(z0, auto_rotate=False)
            != pipeline._zones_signature(z0, auto_rotate=True))
