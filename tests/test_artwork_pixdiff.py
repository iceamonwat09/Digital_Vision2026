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
