# -*- coding: utf-8 -*-
"""
เทสต์ชั้นเทียบพิกเซล (artwork_check.pixdiff) — advisory ล้วน.

ทั้งหมด deterministic: สร้าง PDF/ภาพเองในเทสต์ ไม่ต้องมีไฟล์จริง ไม่ต่อเน็ต
และไม่แตะ ``data/``.
"""

import json
import os

import numpy as np
import pytest

fitz = pytest.importorskip("fitz")
cv2 = pytest.importorskip("cv2")

from artwork_check import pixdiff


LINES = ["TUNA IN SUNFLOWER OIL", "NET WEIGHT {net} g", "PRODUCT OF THAILAND"]


def make_pdf(path, net=170, page_w=842, page_h=595, shift_pt=0.0):
    """3 แผงเหมือนกัน — ความต่างอยู่ที่แผงกลาง (col == 1) เท่านั้น"""
    doc = fitz.open()
    page = doc.new_page(width=page_w, height=page_h)
    for col in range(3):
        x = 40 + col * 260
        for i, t in enumerate(LINES):
            mid = col == 1
            dy = shift_pt if (mid and i == 2) else 0.0
            page.insert_text((x, 60 + i * 16 + dy),
                             t.format(net=net if mid else 170),
                             fontsize=9, fontname="helv")
    doc.save(str(path))
    doc.close()


@pytest.fixture
def pair(tmp_path):
    a = tmp_path / "new.pdf"
    b = tmp_path / "old.pdf"
    return a, b


# ── false positive = 0 คือข้อที่สำคัญที่สุดของชั้นนี้ ────────────────
def test_identical_files_report_zero(pair):
    a, b = pair
    make_pdf(a)
    make_pdf(b)
    res = pixdiff.compare_files(str(a), str(b))
    assert res["status"] == pixdiff.OK
    assert res["diff_px"] == 0
    assert res["region_count"] == 0
    assert res["regions"] == []


def test_repeated_runs_are_deterministic(pair):
    a, b = pair
    make_pdf(a, net=170)
    make_pdf(b, net=185)
    r1 = pixdiff.compare_files(str(a), str(b))
    r2 = pixdiff.compare_files(str(a), str(b))
    assert (r1["diff_px"], r1["region_count"]) == (r2["diff_px"], r2["region_count"])


# ── ความไว + ชี้ถูกจุด ───────────────────────────────────────────────
def test_changed_digit_is_found(pair):
    a, b = pair
    make_pdf(a, net=170)
    make_pdf(b, net=185)
    res = pixdiff.compare_files(str(a), str(b))
    assert res["status"] == pixdiff.OK
    assert res["region_count"] >= 1


def test_region_points_at_the_changed_panel_only(pair):
    """กรอบต้องอยู่ที่แผงกลาง — กรอบที่ชี้แผงที่ไม่ได้แก้คือ false positive"""
    a, b = pair
    make_pdf(a, net=170)
    make_pdf(b, net=185)
    res = pixdiff.compare_files(str(a), str(b))
    for r in res["regions"]:
        assert 0.30 <= r["bbox"][0] <= 0.70, r["bbox"]


def test_sub_point_shift_is_found(pair):
    """ขยับ 0.8pt = เล็กกว่าที่คนสังเกตเห็น แต่ต้องจับได้"""
    a, b = pair
    make_pdf(a)
    make_pdf(b, shift_pt=0.8)
    res = pixdiff.compare_files(str(a), str(b))
    assert res["status"] == pixdiff.OK
    assert res["region_count"] >= 1


# ── ด่านความปลอดภัย: ไม่มั่นใจ = ไม่เทียบ (กฎเหล็กข้อ 2) ────────────
def test_different_page_size_is_skipped_not_compared(pair):
    a, b = pair
    make_pdf(a)
    make_pdf(b, page_w=1191, page_h=842)      # A3
    res = pixdiff.compare_files(str(a), str(b))
    assert res["status"] == pixdiff.SKIPPED
    assert res["reason"] == "page_size_mismatch"
    # ห้ามมีผลลัพธ์หลอกออกมาในเคสที่ไม่ได้เทียบ
    assert res["regions"] == []
    assert res["diff_px"] == 0


def test_skip_message_carries_both_real_sizes(pair):
    a, b = pair
    make_pdf(a)
    make_pdf(b, page_w=1191, page_h=842)
    res = pixdiff.compare_files(str(a), str(b))
    assert res["page_size_a"][0] == pytest.approx(297.0, abs=1.0)
    assert res["page_size_b"][0] == pytest.approx(420.0, abs=1.0)
    assert "297" in res["message"] and "420" in res["message"]


def test_tiny_page_size_difference_within_tolerance_still_compares(pair):
    a, b = pair
    make_pdf(a, page_w=842, page_h=595)
    make_pdf(b, page_w=842.4, page_h=595)     # ต่าง 0.4pt = 0.14mm < 0.2mm
    res = pixdiff.compare_files(str(a), str(b))
    assert res["status"] == pixdiff.OK


def test_page_size_tolerance_stays_inside_pixel_tolerance():
    """สองด่านต้องสอดคล้องกัน — ไม่งั้นจะมีเคสที่ผ่านด่าน mm แล้วไปตกด่าน
    พิกเซล ซึ่งให้เหตุผลกับผู้ใช้คนละเรื่องกับสาเหตุจริง"""
    px = pixdiff.PAGE_SIZE_TOL_MM / 25.4 * pixdiff.PIXDIFF_DPI
    assert px <= pixdiff.PIXEL_SIZE_TOL


def test_whole_page_shift_is_refused_not_reported_as_500_regions():
    """ทั้งหน้าเลื่อน = ต่างเกือบทุกพิกเซล — ต้องบอกว่าเทียบไม่ได้
    ไม่ใช่พ่นกรอบเป็นร้อยให้ผู้ตรวจไล่"""
    a = np.random.RandomState(0).randint(0, 255, (300, 300, 3), dtype=np.uint8)
    b = np.roll(a, 7, axis=1)
    res = pixdiff.compare_images(a, b)
    assert res["status"] == pixdiff.SKIPPED
    assert res["reason"] == "too_different"
    assert res["regions"] == []
    assert res["diff_ratio"] > pixdiff.MAX_DIFF_RATIO   # ยังบอกตัวเลขจริง


def test_missing_file_says_so_instead_of_blaming_the_pdf(tmp_path):
    """path ผิดคือสาเหตุที่พบบ่อยที่สุด — ถ้ารายงานเป็น 'เรนเดอร์ไม่สำเร็จ'
    ผู้ใช้จะไปไล่ปัญหาที่ตัวไฟล์ PDF แทนที่จะแก้ชื่อไฟล์"""
    a = tmp_path / "a.pdf"
    make_pdf(a)
    res = pixdiff.compare_files(str(a), str(tmp_path / "ไม่มีอยู่จริง.pdf"))
    assert res["status"] == pixdiff.SKIPPED
    assert res["reason"] == "file_not_found"
    assert "ไม่มีอยู่จริง.pdf" in res["message"]


def test_both_files_missing_are_all_listed(tmp_path):
    res = pixdiff.compare_files(str(tmp_path / "x.pdf"), str(tmp_path / "y.pdf"))
    assert res["reason"] == "file_not_found"
    assert len(res["missing"]) == 2


def test_pdf_vs_image_is_skipped(tmp_path):
    pdf = tmp_path / "a.pdf"
    png = tmp_path / "b.png"
    make_pdf(pdf)
    cv2.imwrite(str(png), np.full((100, 100, 3), 255, np.uint8))
    res = pixdiff.compare_files(str(pdf), str(png))
    assert res["status"] == pixdiff.SKIPPED
    assert res["reason"] == "mixed_type"


def test_images_of_different_size_are_skipped():
    a = np.full((100, 100, 3), 255, np.uint8)
    b = np.full((100, 150, 3), 255, np.uint8)
    res = pixdiff.compare_images(a, b)
    assert res["status"] == pixdiff.SKIPPED
    assert res["reason"] == "raster_size_mismatch"


# ── รายละเอียดที่เคยพลาดได้ง่าย ──────────────────────────────────────
def test_result_is_json_serialisable(pair):
    """ค่าจาก numpy (np.int32/np.float64) ทำให้ json.dumps ระเบิดตอนบันทึก
    report.json — พังหลังบ้านโดยไม่มีใครเห็นจนกว่าจะถึงหน้าเว็บ"""
    a, b = pair
    make_pdf(a, net=170)
    make_pdf(b, net=185)
    res = pixdiff.compare_files(str(a), str(b))
    json.dumps(res)                            # ต้องไม่โยน TypeError
    for r in res["regions"]:
        assert all(isinstance(v, float) for v in r["bbox"])
        assert all(isinstance(v, int) for v in r["px"])
        assert isinstance(r["area_px"], int)


def test_noise_below_threshold_is_ignored():
    """ต่างกันนิดเดียว (anti-alias) ต้องไม่ถูกรายงาน"""
    a = np.full((200, 200, 3), 200, np.uint8)
    b = a.copy()
    b[50:60, 50:60] = 210                      # ต่าง 10 < DIFF_THRESHOLD
    res = pixdiff.compare_images(a, b)
    assert res["status"] == pixdiff.OK
    assert res["region_count"] == 0


def test_small_speck_below_min_area_is_ignored():
    a = np.full((200, 200, 3), 255, np.uint8)
    b = a.copy()
    b[10:12, 10:12] = 0                        # 4 พิกเซล < MIN_REGION_PX
    res = pixdiff.compare_images(a, b)
    assert res["region_count"] == 0


def test_real_block_above_min_area_is_reported():
    a = np.full((200, 200, 3), 255, np.uint8)
    b = a.copy()
    b[100:110, 100:110] = 0                    # 100 พิกเซล
    res = pixdiff.compare_images(a, b)
    assert res["region_count"] == 1
    assert res["regions"][0]["area_px"] == 100


def test_colour_change_at_same_brightness_is_found():
    """เทียบ grayscale อย่างเดียวจะมองไม่เห็นสีที่ luminance ใกล้กัน"""
    a = np.zeros((200, 200, 3), np.uint8)
    a[:] = (255, 255, 255)
    b = a.copy()
    a[50:70, 50:70] = (0, 0, 200)              # แดง
    b[50:70, 50:70] = (200, 0, 0)              # น้ำเงิน
    res = pixdiff.compare_images(a, b)
    assert res["region_count"] == 1


def test_nearby_pixels_merge_into_one_region():
    a = np.full((200, 200, 3), 255, np.uint8)
    b = a.copy()
    b[100:110, 100:110] = 0
    b[100:110, 112:122] = 0                    # ห่าง 2 px = ต้องรวมเป็นก้อนเดียว
    res = pixdiff.compare_images(a, b)
    assert res["region_count"] == 1


def test_far_apart_changes_stay_separate():
    a = np.full((400, 400, 3), 255, np.uint8)
    b = a.copy()
    b[20:35, 20:35] = 0
    b[300:315, 300:315] = 0
    res = pixdiff.compare_images(a, b)
    assert res["region_count"] == 2


def test_regions_sorted_by_area_desc():
    a = np.full((400, 400, 3), 255, np.uint8)
    b = a.copy()
    b[20:30, 20:30] = 0                        # 100 px
    b[300:330, 300:330] = 0                    # 900 px
    res = pixdiff.compare_images(a, b)
    areas = [r["area_px"] for r in res["regions"]]
    assert areas == sorted(areas, reverse=True)


def test_draw_regions_does_not_touch_the_original():
    img = np.full((100, 100, 3), 255, np.uint8)
    before = img.copy()
    out = pixdiff.draw_regions(img, [{"px": [10, 10, 20, 20]}])
    assert np.array_equal(img, before)
    assert not np.array_equal(out, before)


# ── โหมดโซน: เทียบแผงที่จับคู่กัน แม้หน้าคนละขนาด ───────────────────
def make_panel(path, page_w, page_h, at=(60, 80), net=170):
    """ฉลากแผงเดียว ขนาดจริงเท่ากันเสมอ วางบนหน้าขนาดใดก็ได้ ตำแหน่งใดก็ได้"""
    doc = fitz.open()
    page = doc.new_page(width=page_w, height=page_h)
    x0, y0 = at
    page.draw_rect(fitz.Rect(x0, y0, x0 + 220, y0 + 140), color=(0, 0, 0), width=1)
    for i, t in enumerate(LINES):
        page.insert_text((x0 + 10, y0 + 20 + i * 15), t.format(net=net),
                         fontsize=8, fontname="helv")
    doc.save(str(path))
    doc.close()


ZONE_SMALL = [55 / 842.0, 60 / 595.0, 230 / 842.0, 150 / 595.0]
ZONE_BIG = [890 / 2148.0, 680 / 1290.0, 240 / 2148.0, 160 / 1290.0]


@pytest.fixture
def panels(tmp_path):
    small = tmp_path / "a4.pdf"
    big = tmp_path / "big.pdf"
    make_panel(small, 842, 595, at=(60, 80))
    make_panel(big, 2148, 1290, at=(900, 700))
    return small, big


def test_zone_compare_works_across_different_page_sizes(panels):
    """เคสจริงที่พบบ่อยที่สุด: งานเดียวกันบน A4 proof กับแผ่นพิมพ์ใหญ่
    — โหมดทั้งหน้าทำไม่ได้เลย (ขนาดหน้าไม่เท่ากัน)"""
    small, big = panels
    res = pixdiff.compare_zone(str(small), ZONE_SMALL, str(big), ZONE_BIG)
    assert res["status"] == pixdiff.OK
    assert res["region_count"] == 0, res["regions"]


def test_zone_compare_finds_the_edit(tmp_path, panels):
    small, _big = panels
    edited = tmp_path / "big_edit.pdf"
    make_panel(edited, 2148, 1290, at=(900, 700), net=185)
    res = pixdiff.compare_zone(str(small), ZONE_SMALL, str(edited), ZONE_BIG)
    assert res["status"] == pixdiff.OK
    assert res["region_count"] >= 1


def test_zone_compare_reports_shift_instead_of_hiding_it(panels):
    """การเลื่อนที่ align ออกไปต้องรายงานกลับ ไม่ใช่ซ่อน"""
    small, big = panels
    res = pixdiff.compare_zone(str(small), ZONE_SMALL, str(big), ZONE_BIG)
    assert "shift_mm" in res and len(res["shift_mm"]) == 2
    assert "match_score" in res and res["match_score"] > pixdiff.MIN_MATCH_CONF


def test_zone_on_blank_area_refuses_instead_of_guessing(panels):
    small, big = panels
    res = pixdiff.compare_zone(str(small), ZONE_SMALL, str(big),
                               [0.02, 0.02, 0.08, 0.08])
    assert res["status"] == pixdiff.SKIPPED
    assert res["reason"] in ("align_failed", "zone_too_different",
                             "zone_empty", "zone_blank")
    assert res["regions"] == []


def test_blank_zone_never_reports_no_difference(panels):
    """โซนว่าง vs โซนว่าง เคยขึ้น 'ไม่พบความต่าง' ทั้งที่ไม่ได้ตรวจอะไรเลย —
    template สีขาวล้วนจับคู่ได้คะแนนเต็มกับพื้นที่ขาวที่ไหนก็ได้.
    นี่คือความมั่นใจปลอมที่อันตรายที่สุดของชั้นนี้"""
    small, big = panels
    res = pixdiff.compare_zone(str(small), [0.75, 0.75, 0.2, 0.2],
                               str(big), [0.02, 0.02, 0.1, 0.1])
    assert res["status"] == pixdiff.SKIPPED
    assert res["reason"] == "zone_blank"
    assert "หมึก" in res["message"]


def test_zone_compare_needs_pdf(tmp_path, panels):
    small, _ = panels
    png = tmp_path / "x.png"
    cv2.imwrite(str(png), np.full((50, 50, 3), 255, np.uint8))
    res = pixdiff.compare_zone(str(small), ZONE_SMALL, str(png), ZONE_BIG)
    assert res["reason"] == "not_pdf"


def test_zone_compare_missing_file(panels):
    small, _ = panels
    res = pixdiff.compare_zone(str(small), ZONE_SMALL, "ไม่มี.pdf", ZONE_BIG)
    assert res["reason"] == "file_not_found"


def test_scaled_content_is_refused_not_filtered(tmp_path, panels):
    """วัดจากไฟล์จริงแล้ว: สเกลเพี้ยนแค่ 0.2% ทำให้เกิดบริเวณปลอม 54-239 จุด
    และไม่มีค่า min_region ใดกรองออกได้โดยไม่ทิ้งความต่างจริง ⇒ ต้องปฏิเสธ"""
    small, _big = panels
    shrunk = tmp_path / "shrunk.pdf"
    # แผงเดียวกันแต่ย่อลง 8% แล้ววางให้ไม่ชนขอบโซน
    doc = fitz.open()
    page = doc.new_page(width=2148, height=1290)
    x0, y0, k = 900, 700, 0.92
    page.draw_rect(fitz.Rect(x0, y0, x0 + 220 * k, y0 + 140 * k),
                   color=(0, 0, 0), width=1)
    for i, t in enumerate(LINES):
        page.insert_text((x0 + 10 * k, y0 + (20 + i * 15) * k),
                         t.format(net=170), fontsize=8 * k, fontname="helv")
    doc.save(str(shrunk))
    doc.close()

    res = pixdiff.compare_zone(str(small), ZONE_SMALL, str(shrunk),
                               [880 / 2148.0, 690 / 1290.0,
                                260 / 2148.0, 170 / 1290.0])
    assert res["status"] == pixdiff.SKIPPED
    assert res["reason"] in ("scale_mismatch", "align_failed")
    assert res["regions"] == []


def test_identical_panels_are_not_rejected_by_rounding(panels):
    """⚠️ เคยพลาดมาแล้ว: แผงเดียวกันเป๊ะวัดขนาดหมึกได้ 615x421 กับ 614x420
    (คลาด 1 px จากการปัดเศษตอน raster) = 1.0016 เท่า ซึ่งเกินด่านสเกลละเอียด
    ที่ตั้งไว้ 0.00163 ไปนิดเดียว ⇒ ระบบปฏิเสธการเทียบทั้งที่เทียบได้.
    เครื่องมือวัด (bounding box ±1px) หยาบกว่าเกณฑ์ที่มันต้องบังคับ"""
    small, big = panels
    res = pixdiff.compare_zone(str(small), ZONE_SMALL, str(big), ZONE_BIG)
    assert res["status"] == pixdiff.OK, res.get("message")


def test_gross_scale_difference_is_still_refused(tmp_path, panels):
    """แต่สเกลต่างกันชัด ๆ (แบบ A4 proof ย่อ 29.5%) ต้องปฏิเสธเหมือนเดิม"""
    small, _big = panels
    shrunk = tmp_path / "shrunk.pdf"
    doc = fitz.open()
    page = doc.new_page(width=2148, height=1290)
    x0, y0, k = 900, 700, 0.70              # ย่อ 30%
    page.draw_rect(fitz.Rect(x0, y0, x0 + 220 * k, y0 + 140 * k),
                   color=(0, 0, 0), width=1)
    for i, t in enumerate(LINES):
        page.insert_text((x0 + 10 * k, y0 + (20 + i * 15) * k),
                         t.format(net=170), fontsize=8 * k, fontname="helv")
    doc.save(str(shrunk))
    doc.close()
    res = pixdiff.compare_zone(str(small), ZONE_SMALL, str(shrunk),
                               [880 / 2148.0, 690 / 1290.0,
                                260 / 2148.0, 170 / 1290.0])
    assert res["status"] == pixdiff.SKIPPED
    assert res["reason"] in ("scale_mismatch", "align_failed")


def test_measured_scale_is_reported_when_measurable(panels):
    """ความต่างที่เห็นอาจมาจาก 'งานถูกย่อ/ขยาย' — ผู้ตรวจต้องเห็นตัวเลข
    (โซนต้องกว้างพอให้หมึกไม่ชนขอบ ไม่งั้นวัดสเกลไม่ได้ตามออกแบบ)"""
    small, big = panels
    # โซนกว้างกว่าแผงทั้งสองฝั่ง (แผง a อยู่ 60,80-280,220 · b อยู่ 900,700-1120,840)
    wide_small = [50 / 842.0, 70 / 595.0, 240 / 842.0, 170 / 595.0]
    wide_big = [880 / 2148.0, 690 / 1290.0, 260 / 2148.0, 170 / 1290.0]
    res = pixdiff.compare_zone(str(small), wide_small, str(big), wide_big)
    assert res["status"] == pixdiff.OK
    assert "scale_ratio" in res and len(res["scale_ratio"]) == 2
    assert res["scale_ratio"][0] == pytest.approx(1.0, abs=0.01)


def test_scale_allowance_shrinks_as_the_zone_grows():
    """โซนยิ่งใหญ่ ยิ่งทนสเกลเพี้ยนได้น้อย — เพราะความคลาดสะสมตามระยะ"""
    assert pixdiff.scale_allowance(400) > pixdiff.scale_allowance(4000)
    # โซน 800px กับ tolerance 1px → ยอมได้ราว 0.125%
    assert pixdiff.scale_allowance(800, 1) == pytest.approx(0.00125, rel=0.01)


def test_ink_extent_refuses_to_measure_when_clipped():
    """หมึกที่ชนขอบภาพ = เนื้อหาถูกตัด → วัดสเกลจากตรงนี้ไม่ได้
    (เคยทำให้ระบบปฏิเสธการเทียบทั้งที่สเกลเท่ากันเป๊ะ)"""
    img = np.full((100, 100, 3), 255, np.uint8)
    img[30:70, 30:70] = 0                      # อยู่กลาง ไม่ชนขอบ
    assert pixdiff._ink_extent(img) == (40, 40)
    img2 = np.full((100, 100, 3), 255, np.uint8)
    img2[0:70, 30:70] = 0                      # ชนขอบบน
    assert pixdiff._ink_extent(img2) == (0, 0)


def test_content_bbox_reports_real_mm_size(panels):
    """ขนาดจริงของ 'กรอบที่มีหมึก' ต้องเท่ากันแม้หน้าคนละขนาด — นี่คือค่าที่
    ใช้ตอบว่าไฟล์ไหนถูกย่อ"""
    small, big = panels
    _b1, w1, h1 = pixdiff.content_bbox(str(small))
    _b2, w2, h2 = pixdiff.content_bbox(str(big))
    assert w1 == pytest.approx(w2, rel=0.03)
    assert h1 == pytest.approx(h2, rel=0.03)


# ── ความคลาดระดับพิกเซล: จำเป็นกับโหมดโซน ห้ามใช้กับโหมดทั้งหน้า ────
def test_one_pixel_shift_is_noise_with_tolerance_but_real_without():
    a = np.full((120, 120, 3), 255, np.uint8)
    a[40:60, 40:80] = 0
    b = np.roll(a, 1, axis=1)                  # เลื่อน 1 พิกเซล
    strict = pixdiff.compare_images(a, b, tolerance_px=0)
    tolerant = pixdiff.compare_images(a, b, tolerance_px=1)
    assert strict["diff_px"] > 0               # โหมดทั้งหน้า: ถือว่าต่าง
    assert tolerant["region_count"] == 0       # โหมดโซน: ถือว่าเป็น noise


def test_tolerance_does_not_hide_a_real_change():
    a = np.full((120, 120, 3), 255, np.uint8)
    b = a.copy()
    b[40:60, 40:80] = 0                        # บล็อกดำโผล่มาใหม่ทั้งก้อน
    res = pixdiff.compare_images(a, b, tolerance_px=1)
    assert res["region_count"] == 1


def test_tolerant_compare_is_symmetric():
    a = np.full((120, 120, 3), 255, np.uint8)
    a[40:60, 40:80] = 0
    b = np.roll(a, 1, axis=0)
    r1 = pixdiff.compare_images(a, b, tolerance_px=1)
    r2 = pixdiff.compare_images(b, a, tolerance_px=1)
    assert r1["diff_px"] == r2["diff_px"]


# ── ค่าที่ล็อกจากการวัดบนไฟล์จริง 11 ไฟล์ ─────────────────────────
def test_zone_mode_blurs_but_whole_page_does_not():
    """โหมดโซนต้องเบลอ (แก้ noise เศษพิกเซล) · โหมดทั้งหน้าต้องไม่เบลอ
    (สองไฟล์เรนเดอร์ลงกริดเดียวกัน วัดได้ 0 พิกเซลอยู่แล้ว จึงต้องเป๊ะ)"""
    assert pixdiff.ZONE_BLUR_SIGMA == 1.0
    a = np.full((80, 80, 3), 255, np.uint8)
    default_page = pixdiff.compare_images(a, a)
    assert default_page["tolerance_px"] == 0


def test_blur_absorbs_subpixel_noise_that_tolerance_alone_cannot():
    """หัวใจของการตัดสินใจ: เลื่อนเศษพิกเซลบนพื้นไล่เฉด (แบบภาพถ่ายในงานจริง)
    tolerance อย่างเดียวเอาไม่อยู่ แต่เบลอเอาอยู่"""
    grad = np.tile(np.linspace(0, 255, 200, dtype=np.uint8), (200, 1))
    a = cv2.cvtColor(grad, cv2.COLOR_GRAY2BGR)
    # เลื่อนแบบเศษพิกเซลด้วยการ resample (ไม่ใช่ roll ซึ่งเป็นจำนวนเต็ม)
    m = np.float32([[1, 0, 0.5], [0, 1, 0]])
    b = cv2.warpAffine(a, m, (200, 200), flags=cv2.INTER_LINEAR,
                       borderMode=cv2.BORDER_REPLICATE)
    tol_only = pixdiff.compare_images(a, b, tolerance_px=1)
    with_blur = pixdiff.compare_images(a, b, tolerance_px=1, blur_sigma=1.0)
    assert with_blur["diff_px"] <= tol_only["diff_px"]


def test_blur_does_not_hide_a_real_change():
    """เบลอต้องไม่กลบความต่างจริง — วัดบนไฟล์จริงแล้วสัญญาณ 4x2mm
    จับได้ 11/11 และจุด 0.6mm กลับจับได้ดีขึ้น (0/11 -> 7/11)"""
    a = np.full((200, 200, 3), 255, np.uint8)
    a[50:150, 50:150] = (30, 30, 30)
    b = a.copy()
    b[95:105, 95:105] = (0, 0, 255)            # จุดแดง 10x10 กลางบล็อก
    res = pixdiff.compare_images(a, b, tolerance_px=1, blur_sigma=1.0)
    assert res["region_count"] >= 1


def test_reason_text_is_human_readable():
    assert "ขนาดหน้า" in pixdiff.reason_text("page_size_mismatch")
    assert pixdiff.reason_text("something_new") == "something_new"


def test_max_regions_caps_the_report():
    a = np.full((600, 600, 3), 255, np.uint8)
    b = a.copy()
    for i in range(5):
        for j in range(5):
            b[i * 100 + 10:i * 100 + 25, j * 100 + 10:j * 100 + 25] = 0
    res = pixdiff.compare_images(b, a, max_regions=4)
    assert len(res["regions"]) == 4
    assert res["region_count"] == 25           # นับจริงยังบอกครบ
    assert res["truncated"] is True
