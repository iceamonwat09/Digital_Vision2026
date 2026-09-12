# -*- coding: utf-8 -*-
"""โหมดทดลอง "เทียบแผงระดับพิกเซล" (ช่องติ๊ก default ปิด).

``pixdiff.compare_zone`` เดิมตอบว่าเทียบไม่ได้กับไฟล์คู่จริงของสถานี —
ไล่หาสาเหตุแล้วพบ 3 จุด ซึ่งแก้ได้ทั้งหมด:

  ① แผงขยายสองไฟล์พิมพ์คนละขนาด (51.3 vs 60.9 mm = 0.784 เท่า)
  ② สีทั้งใบต่างกัน (ช่อง R ต่างเฉลี่ย +17.5 — คนละ color profile)
  ③ align เลื่อนได้แค่จำนวนเต็มพิกเซล (แถบฉลาก 23.9% → 2.68% เมื่อ sub-pixel)

แก้ครบแล้ววัดบนไฟล์คู่จริง: **1 บริเวณ = เซลล์ 24%/20% พอดี · ฟ้องผิด 0 ·
รันซ้ำได้ bbox เดิมเป๊ะ · เทียบไฟล์กับตัวเอง 0.0000%**

เทสต์นี้สร้าง PDF เองเพื่อจำลองทั้งสามอาการ (ไฟล์จริงอยู่บนสถานี ไม่ได้อยู่ใน repo)
"""
import os

import cv2
import fitz
import numpy as np
import pytest

from artwork_check import panelmatch as PM, pixdiff

LINES = ["Total fat        7 g    10%",
         "Saturated fat    1 g     5%",
         "Cholesterol     50 mg   17%",
         "Sodium         475 mg   {dv}%",
         "Total carbs      0 g     0%",
         "Protein         26 g    52%"]


def _panel(path, dv=20, k=1.0, tint=(0, 0, 0), x0=120, y0=90):
    """แผงโภชนาการหนึ่งแผง — ``k`` = สเกล · ``tint`` = เลื่อนสีทั้งใบ"""
    doc = fitz.open()
    page = doc.new_page(width=600, height=420)
    bg = tuple(min(1.0, 1.0 - t / 255.0) for t in tint)
    page.draw_rect(fitz.Rect(0, 0, 600, 420), color=bg, fill=bg)
    page.draw_rect(fitz.Rect(x0, y0, x0 + 210 * k, y0 + 130 * k),
                   color=(0, 0, 0), width=1)
    for i, t in enumerate(LINES):
        page.insert_text((x0 + 8 * k, y0 + (18 + i * 18) * k),
                         t.format(dv=dv), fontsize=9 * k, fontname="cour")
    doc.save(str(path))
    doc.close()
    return str(path)


ZONE = [100 / 600.0, 70 / 420.0, 300 / 600.0, 180 / 420.0]
# เทสต์ใช้ dpi ต่ำกว่า production (400) เพื่อให้ชุดเทสต์เร็วพอใช้งาน —
# ตรรกะที่ทดสอบไม่ขึ้นกับ dpi (ยืนยันด้วยไฟล์จริงที่ 400 แยกต่างหาก)
TEST_DPI = 220
# ⚠️ ต้องแรงพอให้ทะลุ ``pixdiff.DIFF_THRESHOLD`` (32) จริง ๆ — ไม่งั้นเทสต์
#    "ปรับสี" จะผ่านแม้ถอดการปรับสีออก = เทสต์ที่ไม่มีความหมาย
#    วัดแล้ว: (30,12,0) → 0.0% ของพิกเซลเกินเกณฑ์ · (45,20,5) → 97.3%
TINT = (45, 20, 5)


def compare(a, b, **kw):
    kw.setdefault("dpi", TEST_DPI)
    return PM.compare(a, ZONE, b, kw.pop("zone_b", ZONE), **kw)


@pytest.fixture
def base(tmp_path):
    return _panel(tmp_path / "a.pdf", dv=20)


# ── พื้นการฟ้องผิดต้องเป็นศูนย์ ──────────────────────────────────────

def test_a_panel_against_itself_finds_nothing(base):
    r = compare(base, base)
    assert r["status"] == pixdiff.OK
    assert r["regions"] == []
    assert r["diff_ratio"] == 0.0


def test_the_real_change_is_found(tmp_path, base):
    """เปลี่ยนแค่ตัวเลข %DV ของแถวเดียว — ต้องเจอบริเวณเดียว."""
    other = _panel(tmp_path / "b.pdf", dv=24)
    r = compare(base, other)
    assert r["status"] == pixdiff.OK
    assert len(r["regions"]) == 1


# ── ① แผงคนละขนาด — ด่านเดิมปฏิเสธ ด่านใหม่ต้องเทียบได้ ──────────────

@pytest.mark.parametrize("k", [0.80, 0.90, 1.15])
# หมายเหตุ: แผงต้องอยู่ในกรอบโซนครบทุกค่า k — ถ้าล้นขอบ เนื้อหาจะถูกตัด
# ซึ่งกลายเป็น "คนละเนื้อหา" จริง ๆ (คนละเรื่องกับความสามารถของตัวเทียบ)
def test_panels_printed_at_different_sizes_are_still_compared(tmp_path, base, k):
    """ไฟล์จริงต่างกัน 0.784 เท่า (51.3 vs 60.9 mm) — ด่านเดิมตัดที่ 5%."""
    scaled = _panel(tmp_path / ("s%d.pdf" % int(k * 100)), dv=20, k=k)
    r = compare(base, scaled)
    assert r["status"] == pixdiff.OK, r.get("message")
    assert abs(r["scale"] - 1.0 / k) < 0.06
    assert r["regions"] == []          # เนื้อหาเดียวกัน แค่คนละขนาด


def test_scale_difference_does_not_hide_a_real_change(tmp_path, base):
    """สำคัญกว่าข้อบน: ย่อแล้วต้องยัง **เจอ** ความต่างจริง ไม่ใช่กลืนหาย."""
    scaled = _panel(tmp_path / "s24.pdf", dv=24, k=0.85)
    r = compare(base, scaled)
    assert r["status"] == pixdiff.OK
    assert len(r["regions"]) >= 1


# ── ② สีทั้งใบต่างกัน ────────────────────────────────────────────────

def test_a_global_colour_shift_is_not_reported_as_a_difference(tmp_path, base):
    """วัดบนไฟล์จริง: ช่อง R ต่างเฉลี่ย +17.5 ระหว่างไฟล์ออกแบบกับไฟล์
    โรงพิมพ์ ⇒ ``max ข้ามช่องสี >= 32`` ของ pixdiff ติดทั้งภาพ."""
    tinted = _panel(tmp_path / "t.pdf", dv=20, tint=TINT)
    r = compare(base, tinted)
    assert r["status"] == pixdiff.OK
    assert r["regions"] == []


def test_colour_matching_does_not_hide_a_real_change(tmp_path, base):
    tinted = _panel(tmp_path / "t24.pdf", dv=24, tint=TINT)
    r = compare(base, tinted)
    assert len(r["regions"]) >= 1


def test_match_colors_only_shifts_levels_not_structure():
    a = np.zeros((40, 40, 3), np.uint8) + 100
    a[10:20, 10:20] = 0
    b = np.clip(a.astype(int) + 40, 0, 255).astype(np.uint8)
    out = PM.match_colors(a, b)
    assert abs(float(out.mean()) - float(a.mean())) < 2.0
    # โครงสร้าง (ตำแหน่งของกล่องดำ) ต้องไม่ขยับ
    assert out[15, 15].mean() < out[35, 35].mean()


# ── ③ เลื่อนแบบเศษพิกเซล ────────────────────────────────────────────

def test_sub_pixel_shift_is_absorbed(tmp_path, base):
    """เลื่อนครึ่งพิกเซล — เดิมทำให้ขอบตัวอักษรเรืองทั้งแผง."""
    shifted = _panel(tmp_path / "sh.pdf", dv=20, x0=120.4, y0=90.3)
    r = compare(base, shifted)
    assert r["status"] == pixdiff.OK
    assert r["regions"] == []


def test_refine_align_actually_reduces_the_residual():
    """วัดกลไกตรง ๆ: บนไฟล์จริง การเลื่อนแบบ **จำนวนเต็มพิกเซล** เหลือต่าง
    23.9% ส่วน sub-pixel เหลือ 2.68% (ดีขึ้น 12 เท่า) — เทสต์นี้จำลอง
    ด้วยการเลื่อนครึ่งพิกเซลจริง แล้ววัดว่า ``refine_align`` ลดส่วนต่างลง.
    """
    rng = np.random.default_rng(7)
    base_img = (rng.random((160, 220)) * 255).astype(np.uint8)
    base_img = cv2.GaussianBlur(base_img, (0, 0), 1.2)
    a = cv2.cvtColor(base_img, cv2.COLOR_GRAY2BGR)
    M = np.float32([[1, 0, 0.5], [0, 1, 0.5]])          # เลื่อนครึ่งพิกเซล
    b = cv2.warpAffine(a, M, (a.shape[1], a.shape[0]),
                       flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    before = float(cv2.absdiff(a, b).mean())
    fixed, ecc = PM.refine_align(a, b)
    after = float(cv2.absdiff(a, fixed).mean())
    assert ecc > 0.9
    assert after < before * 0.6, (before, after)


def test_colour_matching_actually_moves_the_levels():
    """วัดกลไกตรง ๆ เช่นกัน — ไฟล์จริงต่างกัน +17.5 ในช่อง R."""
    a = np.full((60, 60, 3), 120, np.uint8)
    a[20:40, 20:40] = 30
    b = np.clip(a.astype(int) + np.array([5, 20, 45]), 0, 255).astype(np.uint8)
    assert int(cv2.absdiff(a, b).max()) > pixdiff.DIFF_THRESHOLD
    out = PM.match_colors(a, b)
    assert int(cv2.absdiff(a, out).max()) <= pixdiff.DIFF_THRESHOLD


# ── "ไม่มั่นใจ = ไม่รายงาน" ─────────────────────────────────────────

def test_completely_different_content_is_refused(tmp_path, base):
    """คนละเนื้อหา = จับคู่ไม่ได้ ⇒ ต้องปฏิเสธ ไม่ใช่พ่นบริเวณเป็นร้อย."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=420)
    page.insert_text((150, 150), "COMPLETELY UNRELATED", fontsize=20)
    other = str(tmp_path / "x.pdf")
    doc.save(other)
    doc.close()
    r = compare(base, other)
    assert r["status"] == pixdiff.SKIPPED
    assert r["regions"] == []


def test_trimming_the_border_removes_edge_artefacts(base, tmp_path):
    """วัดบนไฟล์จริง: ไม่ตัดขอบได้ 4 บริเวณ (จริง 1 + ขยะขอบ 3) ·
    ตัด 10 px เหลือ 1 พอดี. ขอบคือที่เดียวที่ข้อมูลสองฝั่งไม่ทับกันจริง."""
    other = _panel(tmp_path / "b.pdf", dv=24)
    none = compare(base, other, trim_px=0)
    trimmed = compare(base, other)
    assert len(trimmed["regions"]) <= len(none["regions"])
    assert PM.TRIM_PX > 0


# ── ผลต้องหน้าตาเหมือน defect เดิมทุกประการ ─────────────────────────

def test_defects_look_exactly_like_the_text_layer_ones(tmp_path, base):
    other = _panel(tmp_path / "b.pdf", dv=24)
    r = compare(base, other)
    za = {"id": "z1", "label": "แผงหลัก", "group": "A"}
    zb = {"id": "b2", "label": "แผงอ้างอิง", "group": "A"}
    ds = PM.regions_to_defects(r, za, zb)
    assert ds and all(d["class"] == "MISMATCH_PANELS" for d in ds)
    for d in ds:
        # คีย์ชุดเดียวกับที่ checks._defect สร้าง ⇒ การ์ดหน้าตาเหมือนเดิม
        assert {"class", "severity", "zone_id", "message",
                "found", "reference"} <= set(d)
        assert d["zone_id"] == "z1" and d["ref_zone_ids"] == ["b2"]
        assert d["pixel_px"] and len(d["pixel_bbox"]) == 4


def test_no_text_means_no_guess(tmp_path, base):
    """อ่านบริเวณนั้นไม่ได้ ⇒ ``found`` ว่าง + บอกตำแหน่งเป็น mm
    **ห้ามเดาข้อความ** เพราะการ์ดเอา found ไปวาดกรอบแดง (กฎเหล็กข้อ 2)."""
    other = _panel(tmp_path / "b.pdf", dv=24)
    r = compare(base, other)
    ds = PM.regions_to_defects(r, {"id": "z1", "group": "A"},
                               {"id": "b2"}, read_region=None)
    assert all(d["found"] == "" for d in ds)
    assert all("mm" in d["message"] for d in ds)


def test_region_text_is_used_when_ocr_can_read_it(tmp_path, base):
    other = _panel(tmp_path / "b.pdf", dv=24)
    r = compare(base, other)
    ds = PM.regions_to_defects(
        r, {"id": "z1", "group": "A"}, {"id": "b2"},
        read_region=lambda w, px: "24%" if w == "a" else "20%")
    assert ds[0]["found"] == "24%" and ds[0]["reference"] == "20%"


def test_a_failing_reader_never_breaks_the_run(tmp_path, base):
    other = _panel(tmp_path / "b.pdf", dv=24)
    r = compare(base, other)

    def boom(which, px):
        raise RuntimeError("อ่านไม่ได้")
    ds = PM.regions_to_defects(r, {"id": "z1", "group": "A"}, {"id": "b2"}, boom)
    assert ds and ds[0]["found"] == ""


# ── deterministic ────────────────────────────────────────────────────

def test_repeating_the_comparison_gives_the_same_answer(tmp_path, base):
    other = _panel(tmp_path / "b.pdf", dv=24)
    runs = [compare(base, other) for _ in range(3)]
    keys = {tuple(tuple(g["px"]) for g in r["regions"]) for r in runs}
    assert len(keys) == 1, "ผลต้องเหมือนเดิมทุกครั้ง"


def test_compare_result_can_be_saved_to_json(tmp_path, base):
    import json
    other = _panel(tmp_path / "b.pdf", dv=24)
    json.dumps(compare(base, other))


# ── กติกาที่ค้นพบตอนสร้าง (แต่ละข้อคือบั๊กที่เกิดจริงระหว่างทาง) ────────

def test_template_margin_covers_the_whole_scale_range():
    """⚠️ บั๊กที่เกิดจริง: template ใหญ่กว่าภาพ b ที่ย่อแล้ว ⇒ matchTemplate
    ทำไม่ได้และสเกลนั้น **ถูกข้ามไปเงียบ ๆ** ⇒ แผงที่ใหญ่กว่า 1.05 เท่า
    ขึ้นไปหาไม่เจอเลย (NCC ตกเหลือ 0.30-0.44).

    เงื่อนไข: template = (1 - 2f) ของภาพ ต้อง <= SCALE_LO
    """
    assert PM.TEMPLATE_MARGIN_FRAC >= (1.0 - PM.SCALE_LO) / 2.0 - 1e-9
    assert 1.0 - 2 * PM.TEMPLATE_MARGIN_FRAC <= PM.SCALE_LO + 1e-9


def test_the_coarse_search_falls_back_to_full_resolution(monkeypatch, base,
                                                         tmp_path):
    """ค้นแบบหยาบ (1/4) เร็วกว่ามาก แต่เจอ "ยอดปลอม" ได้เพราะที่ความละเอียด
    นั้นตัวหนังสือเละ ⇒ ต้องมีทางถอยไปค้นเต็มช่วงเมื่อคะแนนไม่ผ่าน."""
    import artwork_check.panelmatch as mod
    src = open(mod.__file__, encoding="utf-8").read()
    assert "if best[0] < MIN_SCALE_NCC:" in src
    assert "full = _scan(gray_a, gray_b, SCALE_LO, SCALE_HI" in src


def test_scale_search_returns_integer_offsets(base):
    """⚠️ ชื่อชนกัน: ``mx`` เป็นทั้งคะแนน NCC (จาก minMaxLoc) และระยะขอบ
    แกน x — ทำให้ offset กลายเป็น float แล้ว slice ภาพพัง."""
    from artwork_check import pixdiff as _pd
    img, _ = _pd.render_zone_mm(base, ZONE, TEST_DPI, 0)
    g = PM._to_gray(img)
    scale, loc, ncc = PM.find_scale(g, g)
    assert isinstance(loc[0], int) and isinstance(loc[1], int)
    assert abs(scale - 1.0) < 0.02 and ncc > 0.99


def test_module_never_touches_the_original_pixdiff_path():
    """โหมดนี้ต้องไม่แก้พฤติกรรมของ pixdiff เดิมแม้แต่บรรทัดเดียว."""
    import artwork_check.panelmatch as mod
    src = open(mod.__file__, encoding="utf-8").read()
    for bad in ("pixdiff.ZONE_TOLERANCE_PX =", "pixdiff.MIN_REGION_PX =",
                "pixdiff.DIFF_THRESHOLD ="):
        assert bad not in src


def test_defaults_are_off_in_the_pipeline():
    import inspect as _i
    from artwork_check import pipeline
    assert _i.signature(pipeline.run_inspection
                        ).parameters["pixel_check"].default is False


def test_route_and_page_wire_the_flag():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "artwork_check", "routes.py"),
              encoding="utf-8") as f:
        assert 'body.get("pixel_check")' in f.read()
    with open(os.path.join(root, "templates", "artwork_check.html"),
              encoding="utf-8") as f:
        assert 'id="awPixelCheck"' in f.read()
    with open(os.path.join(root, "static", "js", "artwork_check.js"),
              encoding="utf-8") as f:
        js = f.read()
    assert "pixel_check: pixelCheckOn()" in js
    assert js.count("pixel_check:") == 1          # ปุ่มส่งตรวจสอบเท่านั้น
    assert "html += pixelHtml(rep.pixel);" in js


def test_the_page_says_which_groups_fell_back():
    """ถ้าเทียบพิกเซลไม่ได้แล้วถอยไปใช้ชั้นข้อความ ต้องบอกให้เห็น —
    ไม่งั้นผู้ใช้เข้าใจว่าทุกกลุ่มถูกเทียบด้วยภาพแล้ว (กฎเหล็กข้อ 2)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "static", "js", "artwork_check.js"),
              encoding="utf-8") as f:
        js = f.read()
    i = js.index("function pixelHtml(px)")
    block = js[i:js.index("window.awPixelHtml", i)]
    assert "เทียบไม่ได้" in block and "ใช้ผลชั้นข้อความ" in block


# ══════════════════════════════════════════════════════════════════════
# ชุดที่เพิ่มหลังสร้าง ``verify_compare.py`` (5 ก.ย. 2026)
#
# เครื่องมือวัดจับได้ 2 เรื่องที่ชุดเทสต์เดิม **มองไม่เห็นเลย** เพราะเทสต์
# เดิมลากโซนเท่ากันทั้งสองฝั่งเสมอ ซึ่งไม่ใช่สิ่งที่เกิดบนสถานี:
#
#   ① ``loc`` ติดลบ (เกิดทุกครั้งที่ลากโซน a หลวมกว่า b) ถูก ``max(0, …)``
#      ตัดทิ้ง ⇒ ภาพสองฝั่งเลื่อนกันเท่ากับค่าที่ตัด. วัดบนไฟล์เดียวกัน:
#      หลวม 1 mm ⇒ **ฟ้องผิด 21 บริเวณ** · 3 mm ⇒ 31 · 6 mm ⇒ 47
#      และ **NCC = 1.0000 ทุกเคส** ⇒ ด่าน NCC จับไม่ได้เลย
#   ② ความต่างที่ "ติดขอบพื้นที่เทียบ" = เนื้อหารอบแผงที่ลากเกินเข้ามา
#      วัดบนคู่จริง 10 แบบการลาก: ที่ไม่ติดขอบ = 1 พอดีทุกแบบ (ของจริง)
#      ที่ติดขอบ = 0/4/10/12 (ขยะล้วน) ⇒ แยกได้ 10/10
# ══════════════════════════════════════════════════════════════════════

def _grow(zone, f):
    """ขยายโซนออกทุกด้านเป็นสัดส่วน ``f`` ของหน้า (จำลองการลากหลวม)."""
    x, y, w, h = zone
    return [x - f, y - f, w + 2 * f, h + 2 * f]


def test_dragging_zone_a_loose_must_not_shift_the_comparison(base):
    """ลากโซน a หลวมกว่า b บนไฟล์เดียวกัน ⇒ ต้องไม่เจออะไรเลย.

    นี่คือเคสที่ทำให้ ``loc`` ติดลบ. ถ้ากลับไป ``max(0, loc)`` เทสต์นี้แดง
    ทันที (วัดจริง: ฟ้องผิด 20-40 บริเวณ)
    """
    for f in (0.01, 0.03, 0.05):
        r = PM.compare(base, _grow(ZONE, f), base, ZONE, dpi=TEST_DPI)
        assert r["status"] == pixdiff.OK
        assert r["regions"] == [], "ลากหลวม %.0f%% แล้วฟ้องผิด %d บริเวณ" % (
            f * 100, len(r["regions"]))


def test_dragging_zone_b_loose_must_not_shift_the_comparison(base):
    """ทิศตรงข้าม (``loc`` เป็นบวก) ต้องยังถูกเหมือนเดิม."""
    for f in (0.01, 0.03, 0.05):
        r = PM.compare(base, ZONE, base, _grow(ZONE, f), dpi=TEST_DPI)
        assert r["status"] == pixdiff.OK
        assert r["regions"] == []


def test_the_real_change_survives_every_way_of_dragging(tmp_path, base):
    """ความต่างจริงต้องไม่ขึ้นกับ "มือที่ลากโซน" — เจอครบทุกแบบ."""
    other = _panel(tmp_path / "b.pdf", dv=24)
    for za, zb in [(ZONE, ZONE), (_grow(ZONE, 0.03), ZONE),
                   (ZONE, _grow(ZONE, 0.03)), (_grow(ZONE, 0.03),
                                               _grow(ZONE, 0.03))]:
        r = PM.compare(base, za, other, zb, dpi=TEST_DPI)
        assert r["status"] == pixdiff.OK
        assert len(r["regions"]) == 1, "ลาก %s/%s ได้ %d บริเวณ" % (
            za, zb, len(r["regions"]))


def test_a_difference_that_only_touches_the_edge_is_not_a_panel_difference(
        tmp_path, base):
    """แต้มความต่างไว้ **นอกแผง** (ในวงแหวนที่ลากเกิน) ⇒ ต้องไม่ตัดสิน.

    ``edge_only`` ทำให้ ``pipeline`` ถอยไปใช้ผลชั้นข้อความของกลุ่มนั้น
    ซึ่งดีกว่าทั้งการฟ้องขยะและการบอกว่า "ไม่พบความต่าง"
    """
    marked = str(tmp_path / "edge.pdf")
    doc = fitz.open(base)
    page = doc[0]
    r = page.rect
    z = _grow(ZONE, 0.03)
    # วางจุดชิดมุมซ้ายบนของโซนที่ลากหลวม (= อยู่นอกแผง)
    x = r.x0 + (z[0] + 0.004) * r.width
    y = r.y0 + (z[1] + 0.004) * r.height
    page.draw_rect(fitz.Rect(x, y, x + 10, y + 10), color=(0, 0, 0),
                   fill=(0, 0, 0), width=0)
    doc.save(marked)
    doc.close()

    res = PM.compare(base, z, marked, z, dpi=TEST_DPI)
    assert res["status"] != pixdiff.OK
    assert res["reason"] == "edge_only"
    assert res.get("edge_regions", 0) >= 1
    # ต้องมีคำอธิบายภาษาคนเสมอ ไม่ใช่รหัสเปล่า ๆ
    assert "ขอบ" in pixdiff.reason_text("edge_only")


def test_edge_regions_are_counted_not_hidden(tmp_path, base):
    """ของที่ตกไปเพราะติดขอบ ต้อง **นับไว้** ไม่ใช่หายเงียบ."""
    other = _panel(tmp_path / "b.pdf", dv=24)
    doc = fitz.open(other)
    page = doc[0]
    r = page.rect
    z = _grow(ZONE, 0.03)
    x = r.x0 + (z[0] + 0.004) * r.width
    y = r.y0 + (z[1] + 0.004) * r.height
    page.draw_rect(fitz.Rect(x, y, x + 10, y + 10), color=(0, 0, 0),
                   fill=(0, 0, 0), width=0)
    both = str(tmp_path / "both.pdf")
    doc.save(both)
    doc.close()

    res = PM.compare(base, z, both, z, dpi=TEST_DPI)
    assert res["status"] == pixdiff.OK
    assert len(res["regions"]) == 1          # ของจริงในแผง
    assert res["edge_regions"] >= 1          # ของนอกแผง — รายงานแยก


def test_the_result_carries_the_numbers_needed_to_improve_it(tmp_path, base):
    """ผลต้องมี "ตัวเลขที่เอาไปพัฒนาต่อได้" ไม่ใช่แค่คำตอบ.

    ข้อกำหนดจากผู้ใช้ 5 ก.ย.: ทุกโหมดต้องแสดงข้อมูลที่เอาไปพัฒนาต่อได้
    ไม่ใช่ให้มานั่งเดาจากคำตอบ
    """
    other = _panel(tmp_path / "b.pdf", dv=24)
    r = compare(base, other)
    for k in ("scale", "ncc", "ecc", "diff_ratio", "size", "zone_size",
              "mm_per_px", "trim_px", "min_region_px", "min_region_mm2",
              "areas_mm2", "edge_regions"):
        assert k in r, "ผลไม่มีคีย์ %r ที่ใช้วินิจฉัย" % k
    assert r["areas_mm2"] and r["areas_mm2"][0] > 0
    assert r["min_region_mm2"] > 0


def test_sensitivity_floor_is_below_the_real_defect_that_was_measured():
    """เกณฑ์ขนาดต่ำสุดต้องเล็กกว่าความต่างจริงที่วัดได้อย่างมีระยะเผื่อ.

    ความต่างจริงของคู่ John West วัดได้ **0.246 mm²** ที่ 400 dpi.
    ถ้าเกณฑ์ไปอยู่ใกล้ค่านั้น ระบบจะพลาดแบบ **false negative** ซึ่งเกิดจริง
    มาแล้วบนสถานี (รอบหนึ่งได้ 0 defect) ⇒ ต้องเผื่ออย่างน้อย 3 เท่า
    """
    mmpp = 25.4 / PM.DPI
    floor_mm2 = PM.MIN_REGION_PX * mmpp * mmpp
    assert floor_mm2 <= 0.246 / 3.0, (
        "เกณฑ์ %.3f mm² ใกล้ความต่างจริง 0.246 mm² เกินไป" % floor_mm2)


def test_region_bbox_is_a_fraction_of_the_whole_zone_a(tmp_path, base):
    """``bbox`` ต้องอ้างอิง "โซน a เต็มใบ" แม้พื้นที่ที่เทียบจะเล็กกว่า.

    เมื่อลากโซน a หลวม พื้นที่ที่เทียบได้จริงคือส่วนที่ทับกันเท่านั้น —
    ถ้าคำนวณ bbox จากพื้นที่ทับ กรอบที่ชี้ให้คนดูจะเลื่อนไปจากของจริง
    """
    other = _panel(tmp_path / "b.pdf", dv=24)
    tight = PM.compare(base, ZONE, other, ZONE, dpi=TEST_DPI)
    f = 0.03
    loose = PM.compare(base, _grow(ZONE, f), other, ZONE, dpi=TEST_DPI)
    assert len(tight["regions"]) == len(loose["regions"]) == 1
    # โซนที่กว้างขึ้น (w + 2f) ⇒ สัดส่วนของจุดเดิมต้องขยับไปตามสูตร ไม่ใช่คงเดิม
    bt = tight["regions"][0]["bbox"][0]
    bl = loose["regions"][0]["bbox"][0]
    exp = (bt * ZONE[2] + f) / (ZONE[2] + 2 * f)
    assert abs(bl - exp) < 0.02, "bbox=%.4f ควรใกล้ %.4f" % (bl, exp)


# ══════════════════════════════════════════════════════════════════════
# ชุดที่เพิ่มหลังผลรันจริงบนสถานี 5 ก.ย. 2026 (รอบ 2)
#
# รายงานขึ้น **0 ทุกช่อง** ทั้งที่ OCR สองฝั่งอ่าน `20%` กับ `24%` ต่างกันชัด ๆ
# ไล่แล้วพบ 2 จุดที่ต้องแก้พร้อมกัน:
#
#   ① แผงที่พิมพ์เล็ก (28x29 mm) เรนเดอร์ที่ 400 dpi ได้แค่ 447x457 px ⇒
#      ความต่างจริงเหลือ **5 พิกเซล** ⇒ พบ 0 บริเวณ
#      (ไล่ระดับบนไฟล์จริง: 411px -> 5px ต่าง -> 0 บริเวณ · 562px -> 27px -> 1)
#   ② พบ 0 บริเวณแล้ว **ยังลบ MISMATCH ของชั้นข้อความทิ้งทั้งกลุ่ม**
#      ⇒ ความต่างจริงที่ชั้นข้อความจับได้ หายไปด้วย
# ══════════════════════════════════════════════════════════════════════

_IMG = np.zeros((400, 400, 3), dtype=np.uint8)


def _tiny(path, dv=20):
    """แผงเดียวกันแต่ **พิมพ์เล็ก** — จำลองงานจริงที่แผงมีขนาดไม่กี่ mm."""
    return _panel(path, dv=dv, k=0.34, x0=40, y0=40)


TINY_ZONE = [30 / 600.0, 30 / 420.0, 110 / 600.0, 70 / 420.0]


def test_a_small_panel_is_rendered_big_enough_to_see_the_difference(tmp_path):
    """แผงเล็กต้องถูกเรนเดอร์ใหม่ที่ DPI สูงขึ้นก่อนเทียบ.

    ปิดชั้นนี้ (``MIN_SIDE_PX = 0``) แล้วความต่างจะเล็กจนถูกตัดทิ้ง =
    อาการ false negative ที่เกิดจริงบนสถานี
    """
    a = _tiny(tmp_path / "a.pdf", dv=20)
    b = _tiny(tmp_path / "b.pdf", dv=24)
    r = PM.compare(a, TINY_ZONE, b, TINY_ZONE, dpi=TEST_DPI)
    assert r["status"] == pixdiff.OK
    assert len(r["regions"]) >= 1, "แผงเล็กแล้วมองไม่เห็นความต่างจริง"
    assert r["dpi"] > TEST_DPI, "ควรเรนเดอร์ใหม่ที่ DPI สูงขึ้น"
    assert max(r["size"]) >= PM.MIN_SIDE_PX * 0.9


def test_the_boost_is_reported_so_it_can_be_diagnosed(tmp_path):
    """``dpi``/``mm_per_px`` ที่รายงานต้องเป็นค่า **ที่ใช้จริง** ไม่ใช่ค่าที่ขอ."""
    a = _tiny(tmp_path / "a.pdf", dv=20)
    b = _tiny(tmp_path / "b.pdf", dv=24)
    r = PM.compare(a, TINY_ZONE, b, TINY_ZONE, dpi=TEST_DPI)
    assert abs(r["mm_per_px"] - 25.4 / r["dpi"]) < 1e-3
    # พื้นที่ที่รายงานต้องคิดจาก mm/px ที่ใช้จริง ไม่งั้นเลข mm² ผิด
    assert r["areas_mm2"] and r["areas_mm2"][0] > 0


def test_a_wide_reference_zone_must_not_hide_the_need_for_more_pixels(tmp_path):
    """โซนอ้างอิงที่ลากกว้างต้องไม่กลบความจำเป็นในการเพิ่มความละเอียด.

    ทั้งสองฝั่งเรนเดอร์ที่ mm/px เท่ากัน ⇒ ขนาดภาพสะท้อน "ขนาดโซนที่ลาก"
    ล้วน ๆ. ถ้าใช้ ``max`` ของสองฝั่งตัดสิน โซนอ้างอิงที่กว้างจะทำให้ไม่ boost
    """
    a = _tiny(tmp_path / "a.pdf", dv=20)
    b = _tiny(tmp_path / "b.pdf", dv=24)
    wide = [0.0, 0.0, 1.0, 1.0]                 # ลากทั้งหน้าเป็นฝั่งอ้างอิง
    r = PM.compare(a, TINY_ZONE, b, wide, dpi=TEST_DPI)
    assert r["dpi"] > TEST_DPI, "โซน b ที่กว้างกลบการเพิ่มความละเอียดไป"


def test_pixel_layer_finding_nothing_never_deletes_the_text_layer_result(
        tmp_path, monkeypatch):
    """**เกิดจริงบนสถานี:** ชั้นภาพพบ 0 บริเวณ แล้วลบ MISMATCH ของชั้นข้อความ
    ทิ้งทั้งกลุ่ม ⇒ รายงานขึ้น 0 ทุกช่องทั้งที่มีความต่างจริง.

    "ภาพไม่เห็น" ไม่เท่ากับ "ไม่มี" (กฎเหล็กข้อ 2)
    """
    from artwork_check import pipeline

    src = _panel(tmp_path / "a.pdf", dv=20)
    monkeypatch.setattr(pipeline, "_find_source", lambda d, *a, **k: src)

    zones = [{"id": "z1", "type": "panel", "group": "A", "bbox": ZONE, "doc": "a"},
             {"id": "z2", "type": "panel", "group": "A", "bbox": ZONE, "doc": "b"}]
    text_defect = {"class": "MISMATCH_PANELS", "zone_id": "z1",
                   "found": "24%", "message": "ต่างกันจริง"}

    def _nothing(*a, **k):
        return ({"status": pixdiff.OK, "regions": [], "edge_regions": 0,
                 "scale": 1.0, "ncc": 1.0, "ecc": 1.0, "diff_ratio": 0.0,
                 "size": [400, 400], "dpi": 400, "mm_per_px": 0.0635,
                 "areas_mm2": [], "min_region_mm2": 0.06}, _IMG, _IMG)

    monkeypatch.setattr(pipeline.panelmatch_mod, "compare_ex", _nothing)
    out, info = pipeline._pixel_compare(str(tmp_path), zones, [text_defect])
    assert out == [text_defect], "ชั้นภาพพบ 0 บริเวณ แต่ไปลบผลชั้นข้อความทิ้ง"
    assert info["used"] == 0
    assert info["pairs"][0].get("kept_text_layer") is True


def test_pixel_layer_still_replaces_when_it_has_something_to_say(
        tmp_path, monkeypatch):
    """ชั้นภาพที่ **เจอบริเวณ** ยังแทนที่ผลชั้นข้อความได้เหมือนเดิม."""
    from artwork_check import pipeline

    src = _panel(tmp_path / "a.pdf", dv=20)
    monkeypatch.setattr(pipeline, "_find_source", lambda d, *a, **k: src)
    zones = [{"id": "z1", "type": "panel", "group": "A", "bbox": ZONE, "doc": "a"},
             {"id": "z2", "type": "panel", "group": "A", "bbox": ZONE, "doc": "b"}]
    stale = {"class": "MISMATCH_PANELS", "zone_id": "z1", "found": "ขยะ"}

    def _found(*a, **k):
        return ({"status": pixdiff.OK, "edge_regions": 0, "scale": 1.0,
                 "ncc": 1.0, "ecc": 1.0, "diff_ratio": 0.0001,
                 "size": [400, 400], "dpi": 400, "mm_per_px": 0.0635,
                 "min_region_mm2": 0.06, "areas_mm2": [0.25],
                 "regions": [{"px": [10, 10, 20, 20], "px_a": [10, 10, 20, 20],
                              "bbox": [0.02, 0.02, 0.05, 0.05], "area_px": 61}]},
                _IMG, _IMG)

    monkeypatch.setattr(pipeline.panelmatch_mod, "compare_ex", _found)
    out, info = pipeline._pixel_compare(str(tmp_path), zones, [stale])
    assert stale not in out
    assert len(out) == 1 and out[0]["class"] == "MISMATCH_PANELS"
    assert info["used"] == 1


# ══════════════════════════════════════════════════════════════════════
# ชุดที่เพิ่มหลังผลรันจริงบนสถานี 8 ก.ย. 2026
#
# โหมด pixel จับความต่างจริงได้ 2 อย่าง — อย่างที่สองคือ **ฟอนต์ตัวหนา vs
# ตัวธรรมดา** ของบรรทัดเดียวกัน (ตัวอักษรเหมือนกันเป๊ะ) ซึ่งชั้นเทียบข้อความ
# ไม่มีทางเห็น. แต่การ์ดขึ้นว่า "พบ: Manuf เทียบกับ: Manufa" เพราะครอปตัด
# กลางคำ ⇒ ผู้ตรวจไปตามหาคำสะกดผิดที่ไม่มีอยู่จริง (กฎเหล็กข้อ 2)
# ══════════════════════════════════════════════════════════════════════

def _region(res):
    return (res.get("regions") or [None])[0]


def test_same_text_is_reported_as_a_look_difference_not_a_spelling_one(
        tmp_path, base):
    """ตัวอักษรเหมือนกัน ⇒ ต้องบอกว่า "รูปลักษณ์ต่าง" และ **ห้ามใส่
    found/reference** ซึ่งการ์ดจะแสดงเป็น "พบ X เทียบกับ Y" = อ่านเหมือน
    คำสะกดผิด."""
    other = _panel(tmp_path / "b.pdf", dv=24)
    r = compare(base, other)
    ds = PM.regions_to_defects(
        r, {"id": "z1", "group": "A"}, {"id": "b2"},
        inspect_region=lambda px: {"a": "Manufacturing", "b": "Manufacturing",
                                   "relation": "same",
                                   "look": {"note": "น้ำหนักเส้นต่างกัน 15%"}})
    assert ds and ds[0]["found"] == "" and ds[0]["reference"] == ""
    assert "ตัวอักษรเหมือนกัน" in ds[0]["message"]
    assert "น้ำหนักเส้น" in ds[0]["message"]
    assert ds[0]["pixel_relation"] == "same"


def test_a_truncated_read_never_becomes_found_versus_reference(tmp_path, base):
    """``Manuf`` / ``Manufa`` เป็นสิ่งประดิษฐ์จากการครอป — ห้ามแสดงเป็น
    ความต่างของข้อความ."""
    other = _panel(tmp_path / "b.pdf", dv=24)
    r = compare(base, other)
    ds = PM.regions_to_defects(
        r, {"id": "z1", "group": "A"}, {"id": "b2"},
        inspect_region=lambda px: {"a": "Manuf", "b": "Manufa"})
    assert ds[0]["found"] == "" and ds[0]["reference"] == ""
    assert ds[0]["pixel_relation"] == "truncated"
    assert "ไม่ครบ" in ds[0]["message"]


def test_a_real_text_change_still_shows_found_and_reference(tmp_path, base):
    """ทางเดิมต้องไม่เปลี่ยน — ตัวเลขเปลี่ยนจริงยังโชว์ พบ/เทียบกับ."""
    other = _panel(tmp_path / "b.pdf", dv=24)
    r = compare(base, other)
    ds = PM.regions_to_defects(
        r, {"id": "z1", "group": "A"}, {"id": "b2"},
        inspect_region=lambda px: {"a": "24%", "b": "20%"})
    assert ds[0]["found"] == "24%" and ds[0]["reference"] == "20%"
    assert ds[0]["pixel_relation"] == "different"


def test_an_exploding_inspector_never_breaks_the_run(tmp_path, base):
    other = _panel(tmp_path / "b.pdf", dv=24)
    r = compare(base, other)

    def boom(px):
        raise RuntimeError("พัง")
    ds = PM.regions_to_defects(r, {"id": "z1", "group": "A"}, {"id": "b2"},
                               inspect_region=boom)
    assert ds and ds[0]["found"] == ""


def test_the_region_is_located_in_the_reference_zone_too(tmp_path, base):
    """``bbox_b`` = กรอบเดียวกันในพิกัดของโซนอ้างอิง ⇒ วาดกรอบแดงได้ทั้งสอง
    ฝั่งด้วยพิกัดที่ **วัดมา** ไม่ใช่การค้นหาคำ (ซึ่งล้มเหลวเมื่อครอปตัดคำ)."""
    other = _panel(tmp_path / "b.pdf", dv=24)
    r = compare(base, other)
    g = _region(r)
    assert g and len(g["bbox_b"]) == 4
    assert all(0.0 <= v <= 1.0 for v in g["bbox_b"])
    # โซนสองฝั่งลากเท่ากันและสเกล 1 ⇒ ต้องชี้จุดเดียวกัน
    for u, v in zip(g["bbox"], g["bbox_b"]):
        assert abs(u - v) < 0.05
    ds = PM.regions_to_defects(r, {"id": "z1", "group": "A"}, {"id": "b2"})
    assert len(ds[0]["pixel_bbox_b"]) == 4


@pytest.mark.parametrize("k,bigger", [(1.25, True), (0.75, False)])
def test_bbox_b_follows_the_scale_of_the_reference_panel(tmp_path, base,
                                                         k, bigger):
    """แผงอ้างอิงที่พิมพ์ **ใหญ่กว่า** ⇒ บริเวณเดียวกันกินสัดส่วนของโซน b
    มากกว่า (และกลับกันเมื่อพิมพ์เล็กกว่า).

    ถ้าไม่ย้อนสเกล กรอบแดงฝั่งอ้างอิงจะไปผิดที่ทุกครั้งที่สองแผงพิมพ์คนละ
    ขนาด — ซึ่งเป็นกรณีปกติของงานจริง (วัดได้ 0.784 บนคู่ไฟล์สถานี)
    """
    other = _panel(tmp_path / ("s%.2f.pdf" % k), dv=24, k=k)
    r = PM.compare(base, ZONE, other, ZONE, dpi=TEST_DPI)
    g = _region(r)
    assert r["status"] == pixdiff.OK and g, "จับคู่แผงที่สเกล %.2f ไม่ได้" % k
    if bigger:
        assert g["bbox_b"][2] > g["bbox"][2]
    else:
        assert g["bbox_b"][2] < g["bbox"][2]


# ── กรอบแดงจากพิกัดที่ "วัดมา" (ไม่ต้องค้นหาคำ) ──────────────────────
#
# ⚠️ ชั้นกรอบแดงเดิมทำงานด้วยการ **ค้นหาคำ** จาก ``found`` ⇒ ล้มเหลวทันที
#    เมื่อครอปตัดคำ (ฝั่งอ้างอิงหา "Manufa" ไม่เจอ ⇒ ไม่มีกรอบ) และล้มเหลว
#    กับภาษาที่ไม่มี traineddata. โหมด pixel รู้ตำแหน่งจากการทาบภาพอยู่แล้ว

def test_a_measured_box_is_drawn_without_searching_for_a_word():
    from artwork_check import pipeline
    img = np.full((200, 300, 3), 255, np.uint8)
    out = pipeline._draw_measured_box(img, [0.3, 0.4, 0.2, 0.2])
    assert out is not img and out.shape == img.shape
    # ต้องมีพิกเซลสีแดงเกิดขึ้นจริง (BGR: แดง = ช่อง 2 สูง · 0/1 ต่ำ)
    red = ((out[:, :, 2] > 150) & (out[:, :, 0] < 100) & (out[:, :, 1] < 100))
    assert red.sum() > 0, "ไม่ได้วาดกรอบเลย"
    # และต้องอยู่แถว ๆ ที่สั่ง ไม่ใช่ทั้งภาพ
    ys, xs = np.where(red)
    assert 40 <= xs.min() <= 100 and 60 <= ys.min() <= 110
    assert red.sum() < img.size * 0.2


@pytest.mark.parametrize("bad", [
    None, [], [0.1, 0.2], [0.1, 0.2, 0.3], ["a", "b", "c", "d"],
    [-0.1, 0.2, 0.3, 0.4], [0.1, 0.2, 0.0, 0.4], [0.1, 0.2, 2.0, 0.4],
])
def test_a_bad_box_never_breaks_the_crop(bad):
    """แสดงผลอย่างเดียว — ข้อมูลเพี้ยนต้องคืนภาพเดิม ไม่ใช่พังทั้งการ์ด."""
    from artwork_check import pipeline
    img = np.full((60, 80, 3), 255, np.uint8)
    out = pipeline._draw_measured_box(img, bad)
    assert np.array_equal(out, img)


# ── ด่านตัดสิน: ย้ายจาก "ก่อนทาบด้วย NCC" ไป "หลังทาบด้วย diff_ratio" ──
#
# 🔑 หลักฐานที่ทำให้ต้องย้าย (วัดบนคู่ PURINA ONE ของผู้ใช้):
#      แผงเดียวกันที่ข้อความถูกเขียนใหม่   ncc 0.4012 → ด่านเดิม **ปฏิเสธ**
#      บล็อกไทย เทียบ บล็อกอังกฤษ (คนละบล็อก) ncc 0.6687 → ด่านเดิม **ยอมรับ**
#    ⇒ ของจริงได้คะแนน *ต่ำกว่า* ของปลอม ⇒ ไม่มีเกณฑ์ใดบน NCC ที่ถูกต้องได้
#    ต้นตอ: find_scale ให้คะแนนจากใจกลาง 56% ของโซนเท่านั้น
#    ยืนยันชุด 19 เคสจริง: diff_ratio ของจริง 0.00-19.96% · ของปลอม 33.63-96.95%

def _blank_page(path, text="COMPLETELY UNRELATED"):
    doc = fitz.open()
    page = doc.new_page(width=600, height=420)
    page.insert_text((150, 150), text, fontsize=20)
    doc.save(str(path))
    doc.close()
    return str(path)


DENSE = ["NUTRITION FACTS PER 100 g SERVING SIZE 25 g",
         "Energy 1450 kJ  Total fat 7 g  Saturated 1 g",
         "Cholesterol 50 mg  Sodium 475 mg  {dv}% DV",
         "Total carbohydrate 0 g  Sugars 0 g  Fibre 0 g",
         "Protein 26 g  Calcium 120 mg  Iron 2.4 mg",
         "Vitamin A 350 ug  Vitamin D 5 ug  Zinc 3 mg"] * 4


def _dense(path, body=None, dv=20, band=None):
    """แผงที่ **หมึกแน่นพอ** ให้เข้าระบอบของด่านใหม่.

    ⚠️ ``_panel`` (fixture เดิมของไฟล์นี้) มีหมึกแค่ ~2.9% ⇒ ตกไประบอบ
    "หมึกน้อย" ซึ่งใช้ด่าน NCC แบบเดิม ⇒ **ทดสอบด่านใหม่ไม่ได้เลย**
    แผงจริงในชุดยืนยันมีหมึก 11-44% ต่อฝั่ง
    """
    doc = fitz.open()
    page = doc.new_page(width=600, height=420)
    if band:
        page.draw_rect(fitz.Rect(*band), color=(0.15,) * 3, fill=(0.15,) * 3)
    for i, t in enumerate(body or DENSE):
        page.insert_text((103, 78 + i * 9), t.format(dv=dv),
                         fontsize=8, fontname="cour")
    doc.save(str(path))
    doc.close()
    return str(path)


def test_the_gate_is_on_by_default():
    from artwork_check import config as C
    assert C.PIXEL_ALIGN_GATE is True
    # ค่าที่วัดมาบนชุด 19 เคสจริง: ของจริงสูงสุด 19.96% · ของปลอมต่ำสุด 33.63%
    assert 0.1996 < C.PIXEL_MAX_DIFF < 0.3363


def test_a_dense_fixture_really_is_dense(tmp_path):
    """กันเทสต์ที่ไร้ความหมาย — ถ้า ``_dense`` หลุดไประบอบหมึกน้อยเมื่อไร
    เทสต์ด่านใหม่ทุกตัวจะกลายเป็นการทดสอบทางเดิมโดยไม่มีใครรู้."""
    r = PM.compare(_dense(tmp_path / "a.pdf"), ZONE,
                   _dense(tmp_path / "b.pdf"), ZONE, dpi=TEST_DPI)
    assert r["sparse"] is False


def test_a_panel_rewritten_in_the_middle_is_still_compared(tmp_path):
    """หัวใจของรอบนี้: NCC ต่ำ แต่ทาบติดและต่างกันน้อย ⇒ ต้อง **เทียบได้**.

    จำลองอาการของคู่ PURINA ONE: ใจกลางโซน (= ที่เดียวที่ ``find_scale``
    ให้คะแนน) ถูกเขียนใหม่ทั้งบล็อก ส่วนที่เหลือของแผงเหมือนกัน
    """
    mid = list(DENSE)
    for i in range(8, 16):
        mid[i] = "REWRITTEN BLOCK LINE %02d COMPLETELY NEW TEXT" % i
    a = _dense(tmp_path / "a.pdf")
    b = _dense(tmp_path / "b.pdf", mid)
    r = PM.compare(a, ZONE, b, ZONE, dpi=TEST_DPI)
    assert r["ncc"] < PM.MIN_SCALE_NCC, \
        "fixture ต้องได้ ncc ต่ำ ไม่งั้นเทสต์ไม่ได้ทดสอบอะไรเลย"
    assert r["status"] == pixdiff.OK, "ด่านเดิมปฏิเสธตรงนี้ = บั๊กที่ผู้ใช้เจอ"
    assert r["regions"], "เทียบได้แล้วต้องชี้จุดที่ต่างจริงด้วย"


def test_turning_the_gate_off_restores_the_old_ncc_behaviour(tmp_path,
                                                             monkeypatch):
    """flag ปิด = ทางเดิมเป๊ะ (กฎเหล็กข้อ 1 — ต้องถอยกลับได้เสมอ)."""
    from artwork_check import config as C
    mid = list(DENSE)
    for i in range(8, 16):
        mid[i] = "REWRITTEN BLOCK LINE %02d COMPLETELY NEW TEXT" % i
    a = _dense(tmp_path / "a.pdf")
    b = _dense(tmp_path / "b.pdf", mid)
    assert PM.compare(a, ZONE, b, ZONE, dpi=TEST_DPI)["status"] == pixdiff.OK
    monkeypatch.setattr(C, "PIXEL_ALIGN_GATE", False)
    assert PM.compare(a, ZONE, b, ZONE,
                      dpi=TEST_DPI)["status"] == pixdiff.SKIPPED


def test_too_different_after_alignment_is_refused_with_its_number(tmp_path):
    """ปฏิเสธแล้วต้องบอก **ตัวเลขที่วัดมา** ไม่ใช่เหตุผลลอย ๆ."""
    from artwork_check import config as C
    other = ["ZZZZ QQQQ WWWW XXXX YYYY VVVV UUUU TTTT SSSS RRRR"] * 24
    a = _dense(tmp_path / "a.pdf")
    b = _dense(tmp_path / "b.pdf", other, band=(100, 70, 400, 160))
    r = PM.compare(a, ZONE, b, ZONE, dpi=TEST_DPI)
    assert r["status"] == pixdiff.SKIPPED
    assert r["reason"] == "panel_too_different"
    assert r["sparse"] is False, "ต้องถูกปฏิเสธด้วยด่านใหม่ ไม่ใช่ด่านเดิม"
    assert r["diff_ratio"] > C.PIXEL_MAX_DIFF
    assert r["regions"] == []
    # เหตุผลต้องมีคำอธิบายภาษาไทยให้ผู้ตรวจอ่าน ไม่ใช่โผล่เป็นรหัสดิบ
    assert pixdiff.reason_text("panel_too_different")


def test_a_sparse_zone_keeps_the_old_gate(tmp_path, base):
    """โซนหมึกน้อย: ``diff_ratio`` พูดแทนไม่ได้เชิงโครงสร้าง ⇒ คงด่านเดิม.

    วัดได้: แผงโภชนาการ (หมึก 2.9%) เทียบหน้าที่มีข้อความบรรทัดเดียว
    (2.3%) ⇒ ต่อให้ต่างกันทุกจุด diff_ratio ขึ้นได้แค่ ~5.6% ซึ่งไม่มีทาง
    ถึงเกณฑ์ 25% ⇒ ถ้าปล่อยให้ด่านใหม่ตัดสิน จะยอมรับคนละเนื้อหา
    """
    other = _blank_page(tmp_path / "x.pdf")
    r = compare(base, other)
    assert r["status"] == pixdiff.SKIPPED
    assert r["regions"] == []
    assert r["sparse"] is True
    # และต้องเป็นเพราะหมึกน้อยจริง ไม่ใช่บังเอิญ
    from artwork_check import config as C
    assert r["ink_a"] + r["ink_b"] < C.PIXEL_MIN_INK


def test_a_dense_zone_never_falls_back_to_the_ncc_gate(tmp_path, base):
    """แผงจริงในชุดยืนยันบางที่สุดมีหมึกรวม 26.5% ⇒ ต้องไม่เข้าระบอบหมึกน้อย."""
    r = compare(base, _panel(tmp_path / "b.pdf", dv=24))
    assert r["status"] == pixdiff.OK
    from artwork_check import config as C
    # แผงทดสอบนี้บาง (สังเคราะห์) แต่เกณฑ์ต้องอยู่ต่ำกว่าแผงจริงที่บางสุด
    assert C.PIXEL_MIN_INK < 0.265


def test_full_page_pixdiff_keeps_its_own_cut(tmp_path):
    """ปุ่ม "เทียบภาพเก่า/ใหม่" (โหมดทั้งหน้า) ต้องไม่ถูกแตะเลย.

    ``compare_images`` ได้พารามิเตอร์ ``max_diff_ratio`` ใหม่ — ค่าเริ่มต้น
    ต้องยังเป็น ``MAX_DIFF_RATIO`` เดิม ไม่งั้นโหมดทั้งหน้าเปลี่ยนพฤติกรรม
    """
    import inspect
    sig = inspect.signature(pixdiff.compare_images)
    assert sig.parameters["max_diff_ratio"].default == pixdiff.MAX_DIFF_RATIO
    assert pixdiff.MAX_DIFF_RATIO == 0.20

    a = np.full((200, 200, 3), 255, np.uint8)
    b = a.copy()
    b[:, :120] = 0                                   # ต่างกัน 60% ของภาพ
    assert pixdiff.compare_images(a, b)["status"] == pixdiff.SKIPPED
    # ส่งค่าเองแล้วต้องผ่านด่านนั้นได้ (เส้นทางที่ panelmatch ใช้)
    assert pixdiff.compare_images(a, b, max_diff_ratio=1.01)["status"] == pixdiff.OK


def test_ncc_still_travels_for_the_reader_but_never_decides(tmp_path, base):
    """ncc ยังต้องรายงานอยู่ (ผู้ใช้เอาไปดูประกอบ) แค่ไม่ใช่ตัวตัดสิน."""
    r = compare(base, _panel(tmp_path / "b.pdf", dv=24))
    assert r["ncc"] is not None and r["ecc"] is not None
