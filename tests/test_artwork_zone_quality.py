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
    ("ZONE_MAG_OK", lambda: Z.ZONE_MAG_OK),
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

def test_small_zone_is_boosted_up_to_the_min_side():
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

def test_the_zone_that_lost_arabic_digits_is_flagged_bad():
    q = Z.zone_ocr_quality(bbox_mm(69.8, 66.2), PAGE_W_PT, PAGE_H_PT)
    assert q["level"] == "bad"
    assert q["tiles"] == 4
    assert q["mag"] == pytest.approx(1.0, abs=0.01)
    assert q["downscaled"] is False        # ไม่ได้เสีย dpi จริง แค่ไม่ได้ขยาย


def test_the_zone_that_read_correctly_is_flagged_ok():
    q = Z.zone_ocr_quality(bbox_mm(72.8, 50.4), PAGE_W_PT, PAGE_H_PT)
    assert q["level"] == "ok"
    assert q["tiles"] == 6
    assert q["mag"] == pytest.approx(1.29, abs=0.02)
    assert q["eff_dpi"] > config.OCR_DPI


def test_a_2_mm_difference_across_the_limit_flips_the_verdict():
    """เหตุผลที่ผู้ใช้เห็นว่า "วาดกรอบใหม่แล้วบางครั้งถูก" — โซนยืนคร่อม
    เส้นแบ่งพอดี ต่างกันไม่กี่มิลลิเมตรก็พลิก."""
    over = Z.zone_ocr_quality(bbox_mm(70, 66), PAGE_W_PT, PAGE_H_PT)
    under = Z.zone_ocr_quality(bbox_mm(70, 63), PAGE_W_PT, PAGE_H_PT)
    assert over["level"] == "bad"
    assert under["level"] != "bad"
    assert under["mag"] > over["mag"]


@pytest.mark.parametrize("h_mm,level", [
    (20, "ok"), (40, "ok"), (50, "ok"), (55, "ok"),
    (60, "warn"), (64, "warn"), (65, "bad"), (100, "bad"),
])
def test_measured_ladder_is_locked(h_mm, level):
    assert Z.zone_ocr_quality(bbox_mm(70, h_mm),
                              PAGE_W_PT, PAGE_H_PT)["level"] == level


# ── กับดัก "โซนเกือบจัตุรัส" ที่เกิดจากชั้นเพิ่ม DPI ของเราเอง ─────────
# เจอตอนขับ Chromium จริง: โซนกว้าง 60 mm สูง 62 mm (ด้านสั้น 60 mm =
# ต่ำกว่าเกณฑ์ 65 mm) กลับได้ level "bad" เพราะ OCR_CROP_MIN_SIDE = 1200
# ดันภาพเป็น 1161x1200 ⇒ ด้านสั้น 1161 px ทะลุ 1152 ไปแล้ว.
# เกณฑ์จริงจึงเป็น "ด้านสั้นของภาพที่ส่ง" ไม่ใช่ "ด้านสั้นของโซนเป็น mm"

@pytest.mark.parametrize("h_mm,level", [
    (50, "ok"), (55, "warn"), (60, "bad"), (62, "bad"),
    (64, "warn"), (66, "warn"), (70, "warn"),
])
def test_near_square_zones_are_judged_by_the_sent_image_not_by_mm(h_mm, level):
    q = Z.zone_ocr_quality(bbox_mm(60, h_mm), PAGE_W_PT, PAGE_H_PT)
    assert q["level"] == level
    assert q["short_mm"] <= Z.zone_short_side_limit_mm()   # โซนยังเล็กกว่า 65 mm


def test_bad_near_square_zone_reports_the_real_reason_in_pixels():
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
