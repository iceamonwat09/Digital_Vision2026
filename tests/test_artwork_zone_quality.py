# -*- coding: utf-8 -*-
"""ด่านเตือน "ขนาดโซนนี้ทำให้ OCR เห็นตัวหนังสือเล็กลง" — advisory ล้วน.

ที่มาของตัวเลขทั้งหมด: ไฟล์จริง John West (แผงโภชนาการเดียวกันของสองไฟล์
อ่านด้วย gemini-2.5-flash 3 รอบ) — โซนที่ด้านสั้น 66.2 mm ได้ 4 ไทล์ /
ขยาย 1.00 แล้ว "เลขอาหรับหาย" เกือบทุกรอบ ส่วนโซน 50.4 mm ได้ 6 ไทล์ /
ขยาย 1.29 แล้วอ่านครบทุกรอบ.
"""
import os
import re

import pytest

from artwork_check import config, zones as Z

JS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "static", "js", "artwork_check.js")

# หน้า A4 แนวนอนของไฟล์ทดสอบจริง (297 x 210 mm) เป็นจุด
PAGE_W_PT, PAGE_H_PT = 297 / 25.4 * 72, 210 / 25.4 * 72


def bbox_mm(w_mm, h_mm, x=0.05, y=0.05):
    return [x, y, w_mm / 297.0, h_mm / 210.0]


# ⚠️ การวัดจริงทั้งหมด (John West · 3 รอบ) ทำตอน OCR_CROP_MIN_SIDE = 1200
#    ต่อมาค่านี้ถูกปรับเป็น 1536 (ได้ความละเอียดเพิ่มโดยไม่กิน token เพิ่ม)
#    ⇒ เทสต์ที่ **ล็อกผลวัด** ต้องตรึงค่าเดิมไว้ ไม่งั้นมันจะเลิกทดสอบสิ่งที่
#    วัดมาแล้วเงียบ ๆ. เทสต์ที่ทดสอบ **คุณสมบัติ** (monotone / โซนเล็กไม่โดน
#    เตือน / สองโซนที่โมเดลเห็นเท่ากันต้องได้คำตอบเดียวกัน) ใช้ค่าปัจจุบัน
MEASURED_MIN_SIDE = 1200


@pytest.fixture
def as_measured(monkeypatch):
    monkeypatch.setattr(config, "OCR_CROP_MIN_SIDE", MEASURED_MIN_SIDE)


def _js():
    with open(JS_PATH, encoding="utf-8") as f:
        return f.read()


def _js_const(name):
    m = re.search(r"\b%s\s*=\s*([0-9]+(?:\.[0-9]+)?)" % re.escape(name), _js())
    assert m, "ไม่พบค่าคงที่ %s ในไฟล์ JS" % name
    return float(m.group(1))


# ── ค่าคงที่ต้องตรงกันสองฝั่ง (JS คำนวณตอนลาก · Python เป็นแหล่งความจริง) ─

@pytest.mark.parametrize("name,expected", [
    ("PREVIEW_DPI", lambda: config.PREVIEW_DPI),
    ("OCR_DPI", lambda: config.OCR_DPI),
    ("OCR_CROP_MAX_SIDE", lambda: config.OCR_CROP_MAX_SIDE),
    ("OCR_CROP_MIN_SIDE", lambda: config.OCR_CROP_MIN_SIDE),
    ("OCR_DPI_MAX_FACTOR", lambda: config.OCR_DPI_MAX_FACTOR),
    ("GEM_SMALL_SIDE", lambda: Z.GEM_SMALL_SIDE),
    ("GEM_TILE_DIV", lambda: Z.GEM_TILE_DIV),
    ("GEM_TILE_MIN", lambda: Z.GEM_TILE_MIN),
    ("GEM_TILE_MAX", lambda: Z.GEM_TILE_MAX),
    ("ZONE_DPI_OK", lambda: Z.ZONE_DPI_OK),
    ("ZONE_DPI_BAD", lambda: Z.ZONE_DPI_BAD),
    ("GEM_TOKENS_PER_TILE", lambda: Z.GEM_TOKENS_PER_TILE),
])
def test_js_constants_match_python(name, expected):
    assert _js_const(name) == pytest.approx(float(expected()))


def test_js_still_computes_page_size_from_the_preview_not_a4():
    """เดิม JS hard-code ขนาดหน้าเป็น 842 pt (A4 แนวนอน) แล้วเดาความสูง
    จากสัดส่วนภาพ ⇒ บนงานแผ่นใหญ่ (757 mm) คลาด 2.5 เท่า. ห้ามกลับไปใช้."""
    js = _js()
    # เหลือได้เฉพาะในคอมเมนต์ที่อธิบายว่าทำไมถึงเลิกใช้
    assert "pageW = 842" not in js
    assert "function cropBasePx" in js   # คิดจากขนาด preview จริงแทน
    assert "dpi / PREVIEW_DPI" in js


# ── กฎการหั่นไทล์ (ตามที่ Google ประกาศ) ─────────────────────────────

def test_small_image_is_one_tile():
    assert Z.gemini_tiling(300, 200) == (1, 1.0)


@pytest.mark.parametrize("short,mag", [
    (384, 3.0), (576, 2.0), (768, 1.5), (1152, 1.0), (2000, 1.0),
])
def test_magnification_follows_the_short_side(short, mag):
    _, got = Z.gemini_tiling(short * 1.4, short)
    assert got == pytest.approx(mag, abs=0.01)


def test_magnification_never_drops_below_one():
    for short in (1152, 1500, 3000):
        assert Z.gemini_tiling(short * 1.2, short)[1] >= 1.0


def test_the_limit_is_65_mm_at_the_configured_dpi():
    assert Z.zone_short_side_limit_mm() == pytest.approx(65.0, abs=0.1)


# ── เส้นทางเรนเดอร์ต้องตรงกับ ocr._render_for_ocr ────────────────────

def test_small_zone_is_boosted_up_to_the_min_side(as_measured):
    pw, ph, scale = Z.ocr_crop_size(bbox_mm(20, 15), PAGE_W_PT, PAGE_H_PT)
    assert scale == 1.0
    assert max(pw, ph) == pytest.approx(config.OCR_CROP_MIN_SIDE, abs=1)


def test_the_boost_is_capped_so_a_tiny_zone_stays_tiny():
    pw, ph, _ = Z.ocr_crop_size(bbox_mm(2, 1.5), PAGE_W_PT, PAGE_H_PT)
    # ×4 เพดาน ⇒ ต้องไม่ถูกดันถึง MIN_SIDE
    assert max(pw, ph) < config.OCR_CROP_MIN_SIDE


def test_boost_never_pushes_past_the_max_side():
    for w, h in ((5, 4), (20, 15), (60, 50), (150, 120)):
        pw, ph, _ = Z.ocr_crop_size(bbox_mm(w, h), PAGE_W_PT, PAGE_H_PT)
        assert max(pw, ph) <= config.OCR_CROP_MAX_SIDE + 1


def test_huge_zone_is_downscaled_and_reported_as_such():
    q = Z.zone_ocr_quality(bbox_mm(280, 200, x=0.0, y=0.0),
                           PAGE_W_PT, PAGE_H_PT)
    assert q["downscaled"] is True
    assert q["level"] == "bad"
    assert q["eff_dpi"] < config.OCR_DPI


# ── ล็อกผลที่วัดได้จากไฟล์จริง ───────────────────────────────────────

def test_the_zone_that_lost_arabic_digits_is_flagged_bad(as_measured):
    q = Z.zone_ocr_quality(bbox_mm(69.8, 66.2), PAGE_W_PT, PAGE_H_PT)
    assert q["level"] == "bad"
    assert q["tiles"] == 4
    assert q["mag"] == pytest.approx(1.0, abs=0.01)
    assert q["downscaled"] is False        # ไม่ได้เสีย dpi จริง
    # โซนนี้ตกลงมาที่ "พื้น" ของ OCR_DPI พอดี = ไม่ได้ประโยชน์จากชั้นใดเลย
    assert q["eff_dpi"] == pytest.approx(config.OCR_DPI, abs=2)


def test_the_zone_that_read_correctly_is_flagged_ok(as_measured):
    q = Z.zone_ocr_quality(bbox_mm(72.8, 50.4), PAGE_W_PT, PAGE_H_PT)
    assert q["level"] == "ok"
    assert q["tiles"] == 6
    assert q["mag"] == pytest.approx(1.29, abs=0.02)
    assert q["eff_dpi"] > config.OCR_DPI
    assert q["eff_dpi"] >= Z.ZONE_DPI_OK


def test_a_few_mm_across_the_limit_flips_the_verdict(as_measured):
    """เหตุผลที่ผู้ใช้เห็นว่า "วาดกรอบใหม่แล้วบางครั้งถูก" — โซนยืนคร่อม
    เส้นแบ่งพอดี ต่างกันไม่กี่มิลลิเมตรก็พลิก."""
    over = Z.zone_ocr_quality(bbox_mm(70, 60), PAGE_W_PT, PAGE_H_PT)
    under = Z.zone_ocr_quality(bbox_mm(70, 50), PAGE_W_PT, PAGE_H_PT)
    assert over["level"] == "bad"
    assert under["level"] == "ok"
    assert under["eff_dpi"] > over["eff_dpi"]


@pytest.mark.parametrize("h_mm,level", [
    (20, "ok"), (40, "ok"), (50, "ok"),
    (55, "warn"), (60, "bad"), (65, "bad"), (100, "bad"),
])
def test_measured_ladder_is_locked(as_measured, h_mm, level):
    assert Z.zone_ocr_quality(bbox_mm(70, h_mm),
                              PAGE_W_PT, PAGE_H_PT)["level"] == level


def test_the_ladder_is_monotone():
    """ยิ่งลากโซนใหญ่ ความละเอียดที่โมเดลเห็นต้องลดลงเรื่อย ๆ ห้ามกระโดดกลับ.

    เกณฑ์เดิม (ตัดสินด้วยกำลังขยาย) ให้ 60 mm = bad แต่ 64/66/70 mm = warn
    ⇒ ลากใหญ่ขึ้นแล้ว "ดีขึ้น" ซึ่งเป็นไปไม่ได้ทางกายภาพ = ลายเซ็นว่าตัวชี้วัด
    ผิดตัว. เทสต์นี้กันไม่ให้กลับไปเป็นแบบนั้น.
    """
    prev = None
    for h in range(20, 101, 2):
        q = Z.zone_ocr_quality(bbox_mm(70, h), PAGE_W_PT, PAGE_H_PT)
        if prev is not None:
            assert q["eff_dpi"] <= prev + 1, "โซนสูง %d mm ได้ dpi เพิ่มขึ้น" % h
        prev = q["eff_dpi"]


# ── กับดัก "โซนเกือบจัตุรัส" ที่เกิดจากชั้นเพิ่ม DPI ของเราเอง ─────────
# เจอตอนขับ Chromium จริง: โซนกว้าง 60 mm สูง 62 mm (ด้านสั้น 60 mm =
# ต่ำกว่าเกณฑ์ 65 mm) กลับได้ level "bad" เพราะ OCR_CROP_MIN_SIDE = 1200
# ดันภาพเป็น 1161x1200 ⇒ ด้านสั้น 1161 px ทะลุ 1152 ไปแล้ว.
# เกณฑ์จริงจึงเป็น "ด้านสั้นของภาพที่ส่ง" ไม่ใช่ "ด้านสั้นของโซนเป็น mm"

@pytest.mark.parametrize("h_mm,level", [
    (50, "ok"), (55, "warn"), (60, "warn"), (62, "bad"),
    (64, "bad"), (66, "bad"), (70, "bad"),
])
def test_near_square_zones_are_judged_by_the_sent_image_not_by_mm(as_measured, h_mm, level):
    q = Z.zone_ocr_quality(bbox_mm(60, h_mm), PAGE_W_PT, PAGE_H_PT)
    assert q["level"] == level
    assert q["short_mm"] <= Z.zone_short_side_limit_mm()   # โซนยังเล็กกว่า 65 mm


def test_bad_near_square_zone_reports_the_real_reason_in_pixels(as_measured):
    q = Z.zone_ocr_quality(bbox_mm(60, 62), PAGE_W_PT, PAGE_H_PT)
    assert q["level"] == "bad" and q["downscaled"] is False
    assert q["short_px"] >= q["limit_px"]      # เหตุผลจริงอยู่ที่พิกเซล
    assert q["short_mm"] < q["limit_mm"]       # ไม่ใช่ที่มิลลิเมตร


def test_ui_never_blames_a_fixed_millimetre_number():
    """ป้ายบนกรอบเคยเขียนว่า "เกิน 65 mm" ซึ่งพูดผิดกับโซนเกือบจัตุรัส."""
    js = _js()
    assert "ZONE_LIMIT_MM" not in js
    assert "เกิน 65" not in _hint_source()


def _hint_source():
    """ตัวโค้ดของฟังก์ชันที่สร้างข้อความให้ผู้ใช้อ่าน (ตั้งแต่ป้ายบนกรอบ
    ถึงบรรทัดในแผง properties)."""
    js = _js()
    i = js.index("function zoneQualityTag")
    j = js.index('$("awPropType").addEventListener', i)
    return js[i:j]


def test_ui_text_has_no_markdown_asterisks():
    """textContent ไม่ตีความ markdown — ดอกจันจะโผล่บนจอตรง ๆ
    (กับดักเดิมของ repo: `⇒ **ยังไม่เจอขีดจำกัด**` โผล่บนหน้าถ่ายรัว)."""
    assert "**" not in _hint_source()


def test_quality_reports_both_dimensions_in_mm():
    q = Z.zone_ocr_quality(bbox_mm(72.8, 50.4), PAGE_W_PT, PAGE_H_PT)
    assert q["w_mm"] == pytest.approx(72.8, abs=0.3)
    assert q["h_mm"] == pytest.approx(50.4, abs=0.3)


# ── ต้องไม่พังและต้องไม่ตัดสินเมื่อข้อมูลไม่พอ ────────────────────────

@pytest.mark.parametrize("bbox", [
    [0, 0, 0, 0], [0.1, 0.1, -1, 0.2], ["x", 0, 0.2, 0.2],
])
def test_never_raises_on_a_degenerate_bbox(bbox):
    assert isinstance(Z.zone_ocr_quality(bbox, PAGE_W_PT, PAGE_H_PT), dict)


def test_quality_is_advisory_only_and_never_touches_zone_data():
    z = {"id": "z1", "type": "panel", "group": "A",
         "bbox": bbox_mm(70, 66), "label": "x"}
    before = dict(z)
    Z.zone_ocr_quality(z["bbox"], PAGE_W_PT, PAGE_H_PT)
    assert z == before


# ── คำเตือนต้องพูดถึง "ไทล์" ไม่ใช่ "กำลังขยาย" ──────────────────────────
#
# วัดสองโซนที่ผลอ่านต่างกันจริงแล้วพบว่า **ตัวหนังสือในสายตาโมเดลเท่ากัน**
# (z1 = 71.1 px · b2 = 68.9 px ต่างกัน 3%) เพราะแผงของ z1 ใหญ่กว่าบนแผ่น
# จริง กำลังขยายที่น้อยกว่าจึงหักล้างกันพอดี ⇒ ป้ายเดิม "ไม่ได้ขยาย ✗"
# **บอกสาเหตุผิด** และส่งผู้ใช้ไปลองวิธีที่ "น่าจะขยายได้" (เช่นย่อภาพก่อน
# ส่ง) ซึ่งวัดแล้วเป็น no-op. สิ่งที่ต่างจริงคือโควตา token ต่อบรรทัด.

def test_quality_reports_the_token_budget():
    """จำนวนไทล์คือโควตาความสนใจ — ต้องเป็นตัวเลขที่เอาไปเทียบสองโซนได้."""
    q = Z.zone_ocr_quality(bbox_mm(72.8, 50.4), PAGE_W_PT, PAGE_H_PT)
    assert q["tokens"] == q["tiles"] * Z.GEM_TOKENS_PER_TILE


def test_the_zone_that_reads_correctly_gets_more_tokens():
    """เหตุผลที่แท้จริงของสองโซนในไฟล์จริง — ไม่ใช่ขนาดตัวหนังสือ."""
    bad = Z.zone_ocr_quality(bbox_mm(69.8, 66.2), PAGE_W_PT, PAGE_H_PT)   # z1
    good = Z.zone_ocr_quality(bbox_mm(72.8, 50.4), PAGE_W_PT, PAGE_H_PT)  # b2
    assert bad["tiles"] < good["tiles"]
    assert bad["tokens"] < good["tokens"]


def test_ui_no_longer_claims_the_text_is_not_magnified():
    """ป้ายเดิม "ไม่ได้ขยาย ✗" พูดเกินจริง — ห้ามกลับไปใช้."""
    src = _hint_source()
    assert "ไม่ได้ขยาย" not in src
    assert "ไม่ขยายให้เลย" not in src


def test_ui_states_the_tile_size_not_only_the_count():
    """ป้ายต้องใช้ "ด้านของไทล์" ไม่ใช่ "จำนวนไทล์".

    จำนวนไทล์โตตามพื้นที่โซน ⇒ โซนใหญ่ได้เลขเยอะทั้งที่หยาบที่สุด แล้ว
    ผู้ใช้อ่านว่า "เยอะ = ดี" แล้วไปขยายโซนซึ่งไม่ช่วยเลย. ด้านของไทล์
    ไม่ขึ้นกับพื้นที่ ⇒ เทียบสองโซนกันได้ตรง ๆ (เล็ก = ละเอียด).
    """
    src = _hint_source()
    assert "ไทล์" in src
    assert "tilePx" in src
    assert "tokens" in src          # โควตารวมยังบอกในแผง properties


def test_tile_size_shrinks_as_the_zone_gets_flatter(as_measured):
    """ตัวเลขที่ผู้ใช้เห็นต้องขยับไปทางเดียวกับคำแนะนำ "ลากให้แบนลง"."""
    tall = Z.zone_ocr_quality(bbox_mm(69.8, 66.2), PAGE_W_PT, PAGE_H_PT)
    flat = Z.zone_ocr_quality(bbox_mm(72.8, 50.4), PAGE_W_PT, PAGE_H_PT)
    assert tall["tile_px"] == Z.GEM_TILE_MAX      # ตันแล้ว = level bad
    assert flat["tile_px"] < tall["tile_px"]
    assert tall["level"] == "bad" and flat["level"] == "ok"


def test_a_bigger_zone_gets_more_tiles_but_is_still_judged_bad(as_measured):
    """หลักฐานว่าทำไม "จำนวนไทล์" เป็นป้ายตัดสินไม่ได้."""
    small = Z.zone_ocr_quality(bbox_mm(69.8, 66.2), PAGE_W_PT, PAGE_H_PT)
    big = Z.zone_ocr_quality(bbox_mm(140, 132), PAGE_W_PT, PAGE_H_PT)
    assert big["tiles"] > small["tiles"]          # ไทล์เยอะกว่า
    assert big["tile_px"] == small["tile_px"]     # แต่หยาบเท่ากันเป๊ะ
    assert big["level"] == small["level"] == "bad"


def test_ui_offers_the_band_split_checkbox_as_a_way_out():
    """โหมดหั่นแถบมีแล้ว — คำแนะนำต้องชี้ไปหามัน ไม่ใช่แค่ "ลากใหม่"."""
    assert "หั่นโซนเป็นแถบ" in _hint_source()


def test_number_in_the_hint_is_not_locale_dependent():
    """toLocaleString() ให้ผลต่างกันตาม locale (บาง locale ให้เลขไทย)
    ⇒ ตัวเลขบนคำเตือนต้องคั่นหลักพันเอง.

    (บรรทัดวันที่ของแถบกู้คืนงานใช้ toLocaleString("th-TH") ได้ต่อไป —
    ที่นั่นระบุ locale ไว้ชัด และเป็นวันที่ ไม่ใช่ตัวเลขที่ต้องเทียบกัน)"""
    assert "toLocaleString" not in _hint_source()
    assert "function thousands" in _js()


# ── บั๊กที่ผู้ใช้เจอบนสถานี 4 ก.ย.: ป้ายบอกให้ตัดเนื้อหาทิ้ง ───────────
#
# ผู้ใช้ลากโซนรอบแผงโภชนาการพอดี (28×29 mm) ได้ ✗ แต่พอลากเลยขอบแผงลงไป
# (28×34 mm) ได้ ✓ ⇒ เครื่องมือกำลังบอกให้ "ลากไม่ให้เต็มแผง".
# วัดแล้วพบว่าโมเดลเห็นสองโซนนี้ **ละเอียดเท่ากันเป๊ะ** (~1050 dpi ทั้งคู่)
# เพราะชั้นเพิ่ม DPI ตรึงด้านยาวของภาพที่ส่งไว้ที่ OCR_CROP_MIN_SIDE
# ⇒ กำลังขยายกับความละเอียดต้นทางหักล้างกันพอดี

def test_two_zones_the_model_sees_identically_get_the_same_verdict(as_measured):
    """ที่ค่าตอนที่ผู้ใช้เจอบั๊ก (MIN_SIDE 1200) โมเดลเห็นสองโซนนี้เท่ากันเป๊ะ
    แต่ป้ายให้ ✗ กับ ✓ ⇒ บอกให้ตัดเนื้อหาทิ้ง."""
    tight = Z.zone_ocr_quality(bbox_mm(28, 29), PAGE_W_PT, PAGE_H_PT)  # เต็มแผง
    loose = Z.zone_ocr_quality(bbox_mm(28, 34), PAGE_W_PT, PAGE_H_PT)  # เลยขอบ
    assert tight["eff_dpi"] == pytest.approx(loose["eff_dpi"], rel=0.05)
    assert tight["level"] == loose["level"] == "ok"


@pytest.mark.parametrize("w,h,extra", [
    (28, 29, 5), (28, 29, 12), (40, 30, 8), (60, 40, 15), (70, 50, 20),
])
def test_cropping_content_away_is_never_rewarded(w, h, extra):
    """คุณสมบัติที่ต้องจริงเสมอไม่ว่าจะตั้ง OCR_CROP_MIN_SIDE เท่าไร:

    การลากเลยขอบเนื้อหาออกไป (โซนใหญ่ขึ้นโดยเนื้อหาเท่าเดิม) ต้อง **ไม่เคย**
    ได้คะแนนดีกว่าการลากกระชับ — ไม่งั้นเครื่องมือจะสอนให้ผู้ตรวจตัดเนื้อหา
    ที่ต้องอ่านทิ้ง ซึ่งเป็นความเสียหายที่ร้ายแรงกว่าการไม่เตือนอะไรเลย
    """
    tight = Z.zone_ocr_quality(bbox_mm(w, h), PAGE_W_PT, PAGE_H_PT)
    loose = Z.zone_ocr_quality(bbox_mm(w, h + extra), PAGE_W_PT, PAGE_H_PT)
    assert tight["eff_dpi"] >= loose["eff_dpi"]
    order = {"bad": 0, "warn": 1, "ok": 2}
    assert order[tight["level"]] >= order[loose["level"]]


def test_a_small_zone_is_never_flagged_bad():
    """โซนเล็กได้ประโยชน์เต็มจากชั้นเพิ่ม DPI ⇒ ไม่มีเหตุให้เตือน.

    ถ้าเทสต์นี้แดง แปลว่าเกณฑ์กลับไปตัดสินด้วยรูปทรง (mag/ไทล์) ซึ่งไร้
    ความหมายในย่านนี้ แล้วจะไปบอกผู้ใช้ให้ตัดเนื้อหาทิ้งอีก.
    """
    for w, h in ((28, 29), (29, 30), (20, 20), (35, 35), (40, 30)):
        q = Z.zone_ocr_quality(bbox_mm(w, h), PAGE_W_PT, PAGE_H_PT)
        assert q["level"] != "bad", "%dx%d ได้ %s" % (w, h, q["level"])
        assert q["eff_dpi"] > config.OCR_DPI


def test_eff_dpi_includes_the_small_zone_boost():
    """บั๊กเดิม: eff_dpi คิดจาก ``scale`` ซึ่งรายงานเฉพาะ "ย่อเพราะชนเพดาน"
    ไม่รวมชั้นเพิ่ม DPI ⇒ โซน 28×29 ที่เรนเดอร์จริงที่ ~1050 dpi ถูกรายงาน
    เป็น 450 dpi (ต่ำกว่าความจริง 2.3 เท่า).
    """
    q = Z.zone_ocr_quality(bbox_mm(28, 29), PAGE_W_PT, PAGE_H_PT)
    pw, _, _ = Z.ocr_crop_size(bbox_mm(28, 29), PAGE_W_PT, PAGE_H_PT)
    assert q["eff_dpi"] == pytest.approx(pw / (28 / 25.4) * q["mag"], rel=0.01)
    assert q["eff_dpi"] > 900


def _tag_source():
    """เฉพาะตัว ``zoneQualityTag`` — ป้ายที่ติดบนกรอบตอนลาก."""
    js = _js()
    i = js.index("function zoneQualityTag")
    return js[i:js.index("function renderHlHint", i)]


def test_the_drag_tag_states_the_number_that_decides():
    """ป้ายบนกรอบต้องโชว์ dpi ที่โมเดลเห็น — ไม่ใช่ไทล์/กำลังขยาย ซึ่ง
    ไร้ความหมายกับโซนเล็ก (ดูเทสต์ two_zones_the_model_sees_identically)."""
    tag = _tag_source()
    assert "effDpi" in tag
    assert "dpi" in tag
    assert "q.tiles" not in tag           # จำนวนไทล์โตตามพื้นที่ ⇒ ป้ายไม่ได้
    assert "q.mag" not in tag
    assert "ไม่ได้ขยาย" not in tag


def test_ui_tells_the_user_to_tighten_not_to_crop_content():
    """คำแนะนำต้องไม่ผลักให้ลากเลยขอบเนื้อหา."""
    assert "กระชับ" in _hint_source()


# ── ค่า OCR_CROP_MIN_SIDE = 1536 (4 ก.ย. 2026) ──────────────────────
#
# 1536 = 2 x GEM_TILE_MAX พอดี = ค่าสูงสุดที่ยังอยู่ใน "ขั้น 4 ไทล์"
# ⇒ ได้ความละเอียดเพิ่มโดยไม่กิน token ของ Gemini เพิ่มเลย

def test_min_side_sits_exactly_on_a_tile_boundary():
    """ถ้าเลยขั้นนี้ไปแม้แต่นิดเดียว จำนวนไทล์กระโดดเป็น 9 ทันที
    (token x2.25) — ค่านี้จึงไม่ใช่ตัวเลขที่เลือกมาลอย ๆ."""
    assert config.OCR_CROP_MIN_SIDE % Z.GEM_TILE_MAX == 0
    assert config.OCR_CROP_MIN_SIDE // Z.GEM_TILE_MAX == 2


def test_raising_min_side_to_the_tile_boundary_costs_no_extra_tokens(monkeypatch):
    """หลักฐานว่า 1200 -> 1536 ฟรีจริง — วัดบนโซนจริงทั้งสามแบบ."""
    for w, h in ((28, 29), (69.8, 66.2), (72.8, 50.4)):
        monkeypatch.setattr(config, "OCR_CROP_MIN_SIDE", 1200)
        before = Z.zone_ocr_quality(bbox_mm(w, h), PAGE_W_PT, PAGE_H_PT)
        monkeypatch.setattr(config, "OCR_CROP_MIN_SIDE", 1536)
        after = Z.zone_ocr_quality(bbox_mm(w, h), PAGE_W_PT, PAGE_H_PT)
        assert after["tokens"] == before["tokens"], "%sx%s กิน token เพิ่ม" % (w, h)
        assert after["eff_dpi"] >= before["eff_dpi"]


def test_the_zone_that_lost_arabic_digits_improves_at_the_new_setting():
    """โซนที่วัดว่า "เลขอาหรับหายเกือบทุกรอบ" ที่ 450 dpi ต้องดีขึ้นจริง."""
    q = Z.zone_ocr_quality(bbox_mm(69.8, 66.2), PAGE_W_PT, PAGE_H_PT)
    assert q["eff_dpi"] > config.OCR_DPI          # ไม่ติดพื้นอีกแล้ว
    assert q["level"] != "bad"
    # ⚠️ ยังไม่ถึง ZONE_DPI_OK — ยังไม่เคยยิง Gemini ที่ค่านี้ ⇒ ต้องเป็น
    #    "warn" ไม่ใช่ "ok" (ไม่มั่นใจ = ไม่บอกว่าผ่าน · กฎเหล็กข้อ 2)
    assert q["level"] == "warn"
