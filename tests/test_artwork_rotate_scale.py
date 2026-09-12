# -*- coding: utf-8 -*-
"""หมุนโซน + ปรับสเกลด้วยการเรนเดอร์ + ความหนาหมึกระดับแผง (8 ก.ย. รอบ 5).

ที่มา — วัดบนไฟล์จริงของผู้ใช้ 4 ไฟล์:

* คู่ Cosma **V12↔V13** คืองานเดียวกัน (die-line 663.5×398 เท่ากัน) แต่ V13
  ย่อลงเหลือ **3.41 เท่า** ⇒ ``find_scale`` ค้นได้แค่ 0.60-1.70 ⇒ เดิมตอบ
  ``align_failed`` = **มองไม่เห็นอะไรเลย** ทั้งที่บาร์โค้ดเป็น placeholder
  และรหัสรีไซเคิลต่างกัน 22 → 20
* ฉลากซอง **Salmon** วางตัวหนังสือ "ตั้ง" ทั้งใบ ⇒ หมุนก่อนอ่านได้คำจริง
  93 คำ เทียบกับไม่หมุน 15-26 คำ · **แต่หมุนผิดทาง (270°) เหลือ 6-9 คำ**
  ซึ่งแย่กว่าไม่หมุน ⇒ ปุ่มหมุนที่คน "เห็นด้วยตา" จึงจำเป็น
* แผงข้อความ V12↔V13 หนากว่ากันทั้งแผง ⇒ ขอบตัวอักษรทุกตัวต่าง

⚠️ ทุกข้อต้อง **ไม่กระทบทางเดิม**: ไม่หมุน + อัตราส่วนอยู่ในช่วง = เดิมเป๊ะ
"""
import numpy as np
import pytest

from artwork_check import panelmatch as pm


# ── ① กรอบต้องกลับเป็นพิกัดของโซนที่ "ยังไม่หมุน" ────────────────────
#
# ``pipeline.zone_crop_jpg`` วาดกรอบ **ก่อน** หมุนภาพ ⇒ ถ้าเก็บพิกัดของภาพ
# ที่หมุนแล้ว กรอบแดงจะไปโผล่ผิดที่ **โดยไม่มี error** = บั๊กที่แย่ที่สุด

@pytest.mark.parametrize("ang", [0, 90, 180, 270])
@pytest.mark.parametrize("box", [
    (0.10, 0.20, 0.25, 0.15),
    (0.00, 0.00, 0.05, 0.05),
    (0.70, 0.60, 0.30, 0.40),
])
def test_unrotate_is_the_exact_inverse_of_apply_rotation(ang, box):
    """พิสูจน์กับ ``cv2.rotate`` ตัวจริง ไม่ใช่เชื่อสูตรบนกระดาษ."""
    cv2 = pytest.importorskip("cv2")
    from artwork_check.pdf_ingest import apply_rotation
    H, W = 240, 400
    bx, by = int(box[0] * W), int(box[1] * H)
    bw, bh = max(2, int(box[2] * W)), max(2, int(box[3] * H))
    img = np.zeros((H, W, 3), np.uint8)
    img[by:by + bh, bx:bx + bw] = 255
    rot = apply_rotation(img, ang)
    ys, xs = np.where(rot[:, :, 0] > 0)
    RH, RW = rot.shape[:2]
    seen = [xs.min() / RW, ys.min() / RH,
            (xs.max() - xs.min() + 1) / RW, (ys.max() - ys.min() + 1) / RH]
    back = pm.unrotate_frac_box(seen, ang)
    want = [bx / W, by / H, bw / W, bh / H]
    assert max(abs(a - b) for a, b in zip(back, want)) < 0.006


def test_unrotate_leaves_a_box_alone_when_there_is_no_rotation():
    b = [0.1, 0.2, 0.3, 0.4]
    assert pm.unrotate_frac_box(b, 0) == b


# ── ② อัตราส่วนขนาดโซนต้องไม่ขึ้นกับการหมุน ─────────────────────────
#
# ปุ่ม "หมุนเฉพาะการแสดงผล" ไม่เปลี่ยนพิกัดที่เก็บ แต่โซนใหม่จะได้ ``rotate``
# ติดมา ⇒ ตัวเลขที่ใช้ตัดสินเรื่องสเกลต้องเป็นค่าที่หมุนแล้วไม่เปลี่ยน

def test_the_size_ratio_uses_the_longest_side_so_rotation_cannot_change_it(
        monkeypatch):
    monkeypatch.setattr(pm.pixdiff, "page_size_mm",
                        lambda p, i=0: (100.0, 100.0))
    tall = [0, 0, 0.10, 0.40]          # 10 × 40 mm
    wide = [0, 0, 0.40, 0.10]          # 40 × 10 mm (= tall ที่หมุน 90°)
    small = [0, 0, 0.05, 0.20]         # ครึ่งหนึ่งของ tall
    r1 = pm.zone_scale_ratio("a.pdf", tall, "b.pdf", small)
    r2 = pm.zone_scale_ratio("a.pdf", wide, "b.pdf", small)
    assert r1 == r2 == pytest.approx(2.0, abs=0.01)


def test_an_unreadable_page_size_gives_no_ratio(monkeypatch):
    monkeypatch.setattr(pm.pixdiff, "page_size_mm", lambda p, i=0: None)
    assert pm.zone_scale_ratio("a.png", [0, 0, 1, 1], "b.png", [0, 0, 1, 1]) is None


# ── ③ ปรับสเกลด้วยการเรนเดอร์ — ต้องไม่แตะเคสปกติ ───────────────────

def _sizes(monkeypatch, wa_mm, wb_mm):
    monkeypatch.setattr(pm.pixdiff, "page_size_mm", lambda p, i=0: (100.0, 100.0))
    a = [0, 0, wa_mm / 100.0, wa_mm / 100.0]
    b = [0, 0, wb_mm / 100.0, wb_mm / 100.0]
    return a, b


def test_a_normal_pair_is_never_touched(monkeypatch):
    """อัตราส่วนอยู่ในช่วงที่ค้นเองได้ ⇒ **สอง dpi ต้องเท่ากัน** = ทางเดิม."""
    a, b = _sizes(monkeypatch, 70.0, 60.0)          # 1.17 เท่า
    da, db, ratio, pre = pm._dpi_pair("a.pdf", a, "b.pdf", b, 400)
    assert da == db and pre is False
    assert 0.6 <= ratio <= 1.7


def test_a_reduced_proof_renders_the_small_side_at_a_higher_dpi(monkeypatch):
    """เคส V12/V13 — ฝั่งเล็กต้องได้ dpi สูงขึ้น **และห้ามลด dpi ฝั่งไหน**."""
    a, b = _sizes(monkeypatch, 68.0, 20.0)          # 3.4 เท่า
    da, db, ratio, pre = pm._dpi_pair("a.pdf", a, "b.pdf", b, 400)
    assert pre is True
    assert da >= 400 and db > da            # เพิ่มฝั่งเล็ก ไม่ลดฝั่งใหญ่
    assert ratio == pytest.approx(3.4, abs=0.05)
    # หลังปรับแล้วสเกลที่เหลือต้องอยู่ในช่วงที่ find_scale ค้นได้
    left = ratio / (db / 400.0)
    assert pm.SCALE_LO <= left <= pm.SCALE_HI


def test_the_bigger_side_is_the_one_that_gets_boosted_when_a_is_small(
        monkeypatch):
    a, b = _sizes(monkeypatch, 20.0, 68.0)          # กลับด้าน
    da, db, ratio, pre = pm._dpi_pair("a.pdf", a, "b.pdf", b, 400)
    assert pre is True and da > db


def test_prescaling_never_makes_a_gigantic_image(monkeypatch):
    """แผงใหญ่ + อัตราส่วนสูง ⇒ ต้องชนเพดาน ไม่ใช่เรนเดอร์ภาพหลักหมื่นพิกเซล."""
    monkeypatch.setattr(pm.pixdiff, "page_size_mm", lambda p, i=0: (1000.0, 1000.0))
    a = [0, 0, 0.90, 0.90]                          # 900 mm
    b = [0, 0, 0.02, 0.02]                          # 20 mm  (45 เท่า)
    da, db, ratio, pre = pm._dpi_pair("a.pdf", a, "b.pdf", b, 400)
    px = pm._zone_px("b.pdf", b, db)
    assert max(px) <= pm.PRESCALE_MAX_SIDE_PX + 1
    # ปรับไม่พอ ⇒ ต้องไม่หลอกตัวเองว่าปรับแล้ว
    assert pre is False


# ── ④ ความหนาหมึกระดับแผง ───────────────────────────────────────────

def test_the_same_image_has_no_ink_difference():
    cv2 = pytest.importorskip("cv2")
    img = np.full((200, 200, 3), 255, np.uint8)
    cv2.rectangle(img, (40, 40), (160, 60), (0, 0, 0), -1)
    cv2.rectangle(img, (40, 100), (160, 120), (0, 0, 0), -1)
    d = pm.panel_ink(img, img.copy())
    assert d["ink_pct"] == 0.0 and d["stroke_pct"] == 0.0


def test_a_heavier_panel_is_reported_with_a_positive_percentage():
    cv2 = pytest.importorskip("cv2")
    thin = np.full((200, 200, 3), 255, np.uint8)
    fat = np.full((200, 200, 3), 255, np.uint8)
    for y in (40, 100, 150):
        cv2.rectangle(thin, (30, y), (170, y + 8), (0, 0, 0), -1)
        cv2.rectangle(fat, (30, y), (170, y + 12), (0, 0, 0), -1)
    d = pm.panel_ink(thin, fat)
    assert d["ink_pct"] > 10 and d["stroke_pct"] > 10
    assert d["stroke_b"] > d["stroke_a"]


def test_a_thin_stroke_is_flagged_as_not_reliable():
    """⚠️ เปอร์เซ็นต์ขึ้นกับความละเอียด — วัดคู่เดียวกันได้ 4.3% ที่เส้น 6 px
    แต่ 12.5% ที่เส้น 2.5 px ⇒ เส้นบางต้องติดธงว่าอย่ายกไปพาดหัว."""
    cv2 = pytest.importorskip("cv2")
    a = np.full((200, 200, 3), 255, np.uint8)
    b = np.full((200, 200, 3), 255, np.uint8)
    for y in (40, 80, 120):
        cv2.rectangle(a, (30, y), (170, y + 1), (0, 0, 0), -1)
        cv2.rectangle(b, (30, y), (170, y + 2), (0, 0, 0), -1)
    d = pm.panel_ink(a, b)
    assert d["reliable"] is False
    thick_a = np.full((200, 200, 3), 255, np.uint8)
    thick_b = np.full((200, 200, 3), 255, np.uint8)
    for y in (40, 90, 140):
        cv2.rectangle(thick_a, (30, y), (170, y + 10), (0, 0, 0), -1)
        cv2.rectangle(thick_b, (30, y), (170, y + 12), (0, 0, 0), -1)
    assert pm.panel_ink(thick_a, thick_b)["reliable"] is True


def test_a_blank_panel_reports_nothing_instead_of_zero():
    """ไม่มีหมึกให้วัด = **ไม่รู้** ไม่ใช่ "เท่ากัน" (กฎเหล็กข้อ 2)."""
    blank = np.full((80, 80, 3), 255, np.uint8)
    assert pm.panel_ink(blank, blank) is None


# ── ⑤ ปรับสเกลต้องเป็น "ทางถอย" ไม่ใช่ทางหลัก ───────────────────────
#
# 🐛 บั๊กที่เกิดจริงระหว่างทำ: ตอนแรกปรับสเกล **ก่อน** เรนเดอร์รอบแรก แล้ว
#    เทสต์ ``test_a_wide_reference_zone_must_not_hide_the_need_for_more_pixels``
#    แดงทันที — เพราะ "ขนาดโซนที่ลาก" ไม่เท่ากับ "สเกลของเนื้อหา": ผู้ใช้ที่
#    ลากโซนอ้างอิงหลวมทั้งหน้าได้อัตราส่วนเพี้ยนมาก ทั้งที่เนื้อหาสเกล 1.0
#    และ find_scale จัดการได้อยู่แล้ว ⇒ ปรับตั้งแต่แรก = ทำของที่ใช้ได้พัง

# ใช้ fixture ตัวเดียวกับ ``test_artwork_panelmatch`` — แผงที่มี "ตัวหนังสือ"
# จริง ไม่ใช่เส้นซ้ำ ๆ (ลายซ้ำทำให้ template matching กำกวมและเทสต์ไร้ความหมาย
# — กับดักที่ repo นี้เคยเจอมาแล้ว)
LINES = ["Energy {dv} kcal", "Protein 3.2 g", "Fat 1.1 g", "Carb 12.4 g",
         "Sodium 20 %", "Fibre 0.8 g", "Sugars 4.0 g"]


def _panel(path, dv=20, k=1.0, x0=120, y0=90):
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page(width=600, height=420)
    page.draw_rect(fitz.Rect(x0, y0, x0 + 210 * k, y0 + 130 * k),
                   color=(0, 0, 0), width=1)
    for i, t in enumerate(LINES):
        page.insert_text((x0 + 8 * k, y0 + (18 + i * 18) * k),
                         t.format(dv=dv), fontsize=9 * k, fontname="cour")
    doc.save(str(path))
    doc.close()
    return str(path)


ZONE = [100 / 600.0, 70 / 420.0, 300 / 600.0, 180 / 420.0]


def test_a_loose_reference_zone_still_works_and_is_not_prescaled(tmp_path):
    """โซนอ้างอิงลากทั้งหน้า (อัตราส่วนเพี้ยนมาก) แต่เนื้อหาสเกล 1.0
    ⇒ ต้องเทียบได้เหมือนเดิม และ **ต้องไม่เข้าทางปรับสเกล**."""
    a = _panel(tmp_path / "a.pdf")
    b = _panel(tmp_path / "b.pdf", dv=24)
    whole = [0.0, 0.0, 1.0, 1.0]
    r = pm.compare(a, ZONE, b, whole, dpi=220)
    assert r["status"] == pm.pixdiff.OK, "เคสที่เคยทำงานได้ต้องไม่พัง"
    assert r.get("prescaled") is False
    assert r["dpi"] >= 220


def test_a_normal_pair_never_enters_the_prescale_path(tmp_path, monkeypatch):
    """เคสปกติต้องเรนเดอร์ 2 ครั้งเท่าเดิม — ไม่มีรอบซ้อนของทางถอย."""
    a = _panel(tmp_path / "a.pdf")
    b = _panel(tmp_path / "b.pdf", dv=24)
    zone = ZONE
    calls = []
    orig = pm.pixdiff.render_zone_mm

    def spy(path, bbox, dpi, page=0, **kw):
        calls.append(dpi)
        return orig(path, bbox, dpi, page, **kw)
    monkeypatch.setattr(pm.pixdiff, "render_zone_mm", spy)
    r = pm.compare(a, zone, b, zone, dpi=220)
    assert len(calls) == 2 and len(set(calls)) == 1
    assert r.get("prescaled") is False


# ── ⑥ หมุนทั้งสองฝั่งเท่ากัน = ผลเท่าเดิมทุกหลัก ────────────────────

@pytest.mark.parametrize("ang", [90, 180, 270])
def test_rotating_both_sides_equally_changes_nothing(tmp_path, ang):
    """วัดบนไฟล์จริงแล้วได้ 38 บริเวณ · ต่าง 1.2094% เท่ากันทั้ง 4 มุม
    (หมุน 90° ไม่มีการ resample) ⇒ ค่าเริ่มต้น 0 = ทางเดิมเป๊ะ."""
    a = _panel(tmp_path / "a.pdf")
    b = _panel(tmp_path / "b.pdf", dv=24)
    zone = ZONE
    base = pm.compare(a, zone, b, zone, dpi=220)
    rot = pm.compare(a, zone, b, zone, dpi=220, rotate_a=ang, rotate_b=ang)
    assert rot["status"] == base["status"]
    assert len(rot.get("regions") or []) == len(base.get("regions") or [])
    assert rot["diff_ratio"] == pytest.approx(base["diff_ratio"], abs=1e-6)


def test_the_angle_actually_used_is_reported(tmp_path):
    """ผู้ตรวจต้องเห็นว่าชั้นภาพหมุนไปเท่าไร ไม่ใช่เดาเอง."""
    a = _panel(tmp_path / "a.pdf")
    b = _panel(tmp_path / "b.pdf", dv=24)
    zone = ZONE
    r = pm.compare(a, zone, b, zone, dpi=220, rotate_a=90, rotate_b=90)
    assert r["rotate_a"] == 90 and r["rotate_b"] == 90


def test_zones_in_different_orientations_can_now_be_compared(tmp_path):
    """เคสที่ปุ่มหมุนแก้ให้ได้: ไฟล์อ้างอิงวางตะแคง ⇒ เดิม align ไม่ติดเลย.

    วัดบนไฟล์จริงแล้ว: หมุนฝั่งเดียว 90/180/270 ได้ ncc 0.19/0.29/0.13
    (ต่ำกว่าเกณฑ์ 0.55 ทั้งหมด) ⇒ ปฏิเสธเสมอ = ปลอดภัยแต่ไม่ได้คำตอบ
    """
    cv2 = pytest.importorskip("cv2")
    a = _panel(tmp_path / "a.pdf")
    b = _panel(tmp_path / "b.pdf")           # เนื้อหาเดียวกันเป๊ะ
    orig = pm.pixdiff.render_zone_mm

    def sideways(path, bbox, dpi, page=0, **kw):
        """จำลอง 'ไฟล์อ้างอิงวางตะแคง' — หมุนเฉพาะฝั่ง b เท่านั้น"""
        img, k = orig(path, bbox, dpi, page, **kw)
        if path == b and img is not None:
            img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        return img, k

    pm.pixdiff.render_zone_mm = sideways
    try:
        bad = pm.compare(a, ZONE, b, ZONE, dpi=220)
        good = pm.compare(a, ZONE, b, ZONE, dpi=220, rotate_b=270)
    finally:
        pm.pixdiff.render_zone_mm = orig
    # ⚠️ วัดแล้วว่าบนแผงจำลอง การตะแคงยัง "ผ่านด่าน" ได้ (ncc 0.72) แล้ว
    #    **ฟ้องผิดเป็นชุด** — ต่างจากไฟล์จริงที่ ncc ตก 0.13-0.29 แล้วถูก
    #    ปฏิเสธ ⇒ สิ่งที่ต้องล็อกคือ "ตั้งมุมแล้วขยะหายไปหมด" ไม่ใช่
    #    "ตะแคงแล้วต้องถูกปฏิเสธ" (ซึ่งขึ้นกับเนื้อหา)
    assert good["status"] == pm.pixdiff.OK, "ตั้งมุมให้แล้วต้องเทียบได้"
    assert not (good.get("regions") or []), "เนื้อหาเดียวกัน ⇒ ต้องไม่มีความต่าง"
    assert good["diff_ratio"] == pytest.approx(0.0, abs=1e-6)
    assert (len(bad.get("regions") or []) > 0
            or bad["status"] != pm.pixdiff.OK), \
        "ไม่ตั้งมุมแล้วต้องไม่ได้ผลที่สะอาด (ไม่งั้นเทสต์นี้ไม่มีความหมาย)"


# ── ⑦ ระดับ pipeline: ชั้น pixel ต้องใช้ ``rotate`` ของโซน ────────────
#
# ปุ่ม "หมุนเฉพาะการแสดงผล" ตั้งค่านี้ให้โซนใหม่ ⇒ ถ้า ``_pixel_compare``
# ไม่ส่งต่อ ค่าที่ผู้ใช้ตั้งจะมีผลกับ OCR อย่างเดียว ส่วนชั้นภาพยังมองคนละแนว
# (ตรวจไม่เจอเงียบ ๆ — ไม่มี error ให้เห็น)

def _pixel_zones():
    return [{"id": "z1", "type": "panel", "group": "A", "doc": "a",
             "rotate": 90, "bbox": [0.1, 0.1, 0.3, 0.3]},
            {"id": "z2", "type": "panel", "group": "A", "doc": "b",
             "rotate": 270, "bbox": [0.2, 0.2, 0.3, 0.3]}]


def _run_pixel(monkeypatch, zones, tmp_path):
    """เรียก ``_pixel_compare`` จริง โดยดักที่ ``compare_ex`` เพื่อดูค่าที่ส่ง."""
    from artwork_check import pipeline
    seen = {}

    def spy(pa, ba, pb, bb, **kw):
        seen.update(kw)
        return ({"status": "skipped", "reason": "align_failed"}, None, None)
    monkeypatch.setattr(pipeline.panelmatch_mod, "compare_ex", spy)
    monkeypatch.setattr(pipeline, "_find_source",
                        lambda d, base="source": str(tmp_path / (base + ".pdf")))

    class _Doc:
        is_pdf = True
    monkeypatch.setattr(pipeline, "ArtworkDocument", lambda p: _Doc())
    pipeline._pixel_compare(str(tmp_path), zones, [])
    return seen


def test_the_pixel_layer_uses_the_zone_rotation(monkeypatch, tmp_path):
    seen = _run_pixel(monkeypatch, _pixel_zones(), tmp_path)
    assert seen.get("rotate_a") == 90 and seen.get("rotate_b") == 270


def test_a_zone_that_never_went_through_ocr_falls_back_to_no_rotation(
        monkeypatch, tmp_path):
    """``rotate`` ที่ยังเป็น "default"/"auto" (ไม่ใช่องศา) ⇒ ถือเป็น 0
    = ทางเดิมเป๊ะ ไม่ใช่ระเบิด."""
    zs = _pixel_zones()
    zs[0]["rotate"] = "default"
    zs[1]["rotate"] = "auto"
    seen = _run_pixel(monkeypatch, zs, tmp_path)
    assert seen.get("rotate_a") == 0 and seen.get("rotate_b") == 0
