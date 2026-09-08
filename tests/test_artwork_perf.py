# -*- coding: utf-8 -*-
"""ประสิทธิภาพและเพดานความปลอดภัยของการตรวจหนึ่งใบ (8 ก.ย. 2026 รอบ 3).

วัดบนสถานีก่อนแก้: ใบหนึ่งใช้ **47.3 วินาที** โดย ~41 วินาที (87%) เป็นการ
**รอ backend ตอบทีละโซนแบบเรียงกัน** และ ``compare_ex`` เรนเดอร์ทิ้ง 1 รอบ
เสมอ (0.85 วินาที/กลุ่ม)

และมีความเสี่ยงที่ยังไม่มีเพดาน: ``pixdiff`` คืนได้ถึง ``MAX_REGIONS = 200``
บริเวณ × อ่านสองฝั่ง = **400 ครั้ง/ใบ** ที่ ~5 วินาที = 33 นาที + เผาโควตา
"""
import time

import numpy as np
import pytest

from artwork_check import config, ocr, panelmatch as PM, pipeline, pixdiff


# ══════════════════════════════════════════════════════════════════════
# ① เพดานจำนวนครั้งที่อ่านข้อความในโหมด pixel
# ══════════════════════════════════════════════════════════════════════

def _res(n):
    """ผลเทียบปลอมที่มี ``n`` บริเวณ (เรียงตามพื้นที่เหมือน pixdiff ของจริง)."""
    return {"status": pixdiff.OK, "mm_per_px": 0.0635, "size": [900, 900],
            "regions": [{"px": [10 + i, 10, 20, 20],
                         "px_a": [10 + i, 10, 20, 20],
                         "bbox": [0.01, 0.01, 0.02, 0.02],
                         "bbox_b": [0.01, 0.01, 0.02, 0.02],
                         "area_px": 500 - i} for i in range(n)]}


def test_the_number_of_ocr_calls_is_capped():
    """200 บริเวณต้องไม่กลายเป็น 400 ครั้งของ backend."""
    calls = []

    def spy(px):
        calls.append(tuple(px))
        return {"a": "x", "b": "y", "relation": "different"}

    ds = PM.regions_to_defects(_res(50), {"id": "z1", "group": "A"},
                               {"id": "b2"}, inspect_region=spy,
                               max_inspect=12)
    assert len(calls) == 12, "อ่านเกินเพดาน (%d ครั้ง)" % len(calls)
    assert len(ds) == 50, "บริเวณที่เกินเพดานต้องยัง **รายงานครบ**"


def test_the_biggest_regions_are_the_ones_that_get_read():
    """บริเวณใหญ่สุด = สำคัญสุด ต้องได้อ่านก่อน (pixdiff เรียงให้แล้ว)."""
    seen = []

    def spy(px):
        seen.append(px[0])
        return {"a": "x", "b": "y", "relation": "different"}

    PM.regions_to_defects(_res(20), {"id": "z1", "group": "A"}, {"id": "b2"},
                          inspect_region=spy, max_inspect=5)
    assert seen == [10, 11, 12, 13, 14]


def test_regions_over_the_cap_say_why_instead_of_guessing():
    """ต้องบอกว่า "ไม่ได้อ่านเพราะมีจำนวนมาก" ไม่ใช่ "อ่านไม่ได้" —
    คนละสาเหตุ คนละวิธีแก้ (กฎเหล็กข้อ 2)."""
    ds = PM.regions_to_defects(
        _res(4), {"id": "z1", "group": "A"}, {"id": "b2"},
        inspect_region=lambda px: {"a": "x", "b": "y", "relation": "different"},
        max_inspect=2)
    assert ds[0]["found"] and ds[1]["found"]
    for d in ds[2:]:
        assert d["found"] == "" and d["pixel_relation"] == "unread"
        assert "จำนวนมาก" in d["message"]


def test_no_cap_means_the_old_behaviour():
    calls = []
    PM.regions_to_defects(_res(30), {"id": "z1", "group": "A"}, {"id": "b2"},
                          inspect_region=lambda px: calls.append(1) or
                          {"a": "x", "b": "y"}, max_inspect=0)
    assert len(calls) == 30


def test_the_shipped_cap_is_small_enough_to_be_safe():
    """เพดานจริงต้องทำให้เวลาสูงสุดอยู่ในระดับที่ยอมรับได้."""
    cap = config.PIXEL_MAX_OCR_REGIONS
    assert 0 < cap <= 20
    # 2 ฝั่ง x ~5 วินาที/ครั้ง แต่ยิงพร้อมกัน ⇒ ~5 วินาที/บริเวณ
    assert cap * 5 <= 120, "เพดานสูงเกินไป (%d บริเวณ)" % cap


# ══════════════════════════════════════════════════════════════════════
# ② เวลารวมสูงสุด — หมดเวลาแล้วต้องไม่ทิ้งผลที่ได้แล้ว
# ══════════════════════════════════════════════════════════════════════

def test_a_timeout_skips_the_extra_layers_but_keeps_every_defect(
        tmp_path, monkeypatch):
    """หมดเวลา ⇒ ข้ามชั้นเสริม **แต่ defect ของชั้นข้อความต้องอยู่ครบ**."""
    import fitz
    f = tmp_path / "a.pdf"
    doc = fitz.open(); doc.new_page(width=300, height=200)
    doc.save(str(f)); doc.close()
    monkeypatch.setattr(pipeline, "_find_source", lambda d, *a, **k: str(f))

    zones = [{"id": "z1", "type": "panel", "group": "A",
              "bbox": [0.1, 0.1, 0.3, 0.3], "doc": "a"},
             {"id": "z2", "type": "panel", "group": "A",
              "bbox": [0.1, 0.1, 0.3, 0.3], "doc": "b"}]
    keep = {"class": "MISMATCH_PANELS", "zone_id": "z1", "found": "24%"}
    called = []
    monkeypatch.setattr(pipeline.panelmatch_mod, "compare_ex",
                        lambda *a, **k: called.append(1) or (None, None, None))

    out, info = pipeline._pixel_compare(str(tmp_path), zones, [keep],
                                        deadline=time.time() - 1)
    assert out == [keep], "หมดเวลาแล้วไปทิ้ง defect ของชั้นข้อความ"
    assert not called, "หมดเวลาแล้วยังเรียกเทียบพิกเซลอยู่"
    assert info["stopped"] and "หมดเวลา" in info["stopped"]
    assert info["pairs"][0]["reason"] == "timeout"


def test_no_deadline_means_the_old_behaviour(tmp_path, monkeypatch):
    import fitz
    f = tmp_path / "a.pdf"
    doc = fitz.open(); doc.new_page(width=300, height=200)
    doc.save(str(f)); doc.close()
    monkeypatch.setattr(pipeline, "_find_source", lambda d, *a, **k: str(f))
    zones = [{"id": "z1", "type": "panel", "group": "A",
              "bbox": [0.1, 0.1, 0.3, 0.3], "doc": "a"},
             {"id": "z2", "type": "panel", "group": "A",
              "bbox": [0.1, 0.1, 0.3, 0.3], "doc": "b"}]
    called = []

    def fake(*a, **k):
        called.append(1)
        return ({"status": "skipped", "reason": "align_failed",
                 "regions": []}, None, None)
    monkeypatch.setattr(pipeline.panelmatch_mod, "compare_ex", fake)
    pipeline._pixel_compare(str(tmp_path), zones, [], deadline=None)
    assert called, "ไม่ได้ตั้ง deadline แต่ข้ามการเทียบ"


def test_the_shipped_timeout_is_generous_but_finite():
    assert config.INSPECT_TIMEOUT_S == 0 or config.INSPECT_TIMEOUT_S >= 120


# ══════════════════════════════════════════════════════════════════════
# ③ ยิง OCR ขนาน — ผลต้องเท่าเดิมทุกประการ
# ══════════════════════════════════════════════════════════════════════

class _Doc:
    """เอกสารปลอมที่ "ช้า" เหมือนรอ backend จริง."""
    is_pdf = True


def _slow_read(delay=0.05):
    def rz(doc, z, **kw):
        time.sleep(delay)
        return {"zone_id": z["id"], "text": "T-" + z["id"], "engine": "n8n",
                "conf": 0.9, "rotate": 0}
    return rz


ZS = [{"id": "z%d" % i, "type": "panel", "bbox": [0, 0, 1, 1]}
      for i in range(8)]


def test_parallel_reading_keeps_the_order_of_the_zones(monkeypatch):
    """ลำดับผลต้องตามลำดับโซนเสมอ — ไม่ใช่ลำดับที่ตอบกลับมาก่อน
    ไม่งั้น report.json/แท็บแปลจะสลับแถวไปมาในแต่ละรอบ."""
    import random

    def rz(doc, z, **kw):
        time.sleep(random.uniform(0, 0.03))
        return {"zone_id": z["id"], "text": "", "engine": "n8n", "rotate": 0}
    monkeypatch.setattr(ocr, "read_zone", rz)
    out = ocr.read_all_zones(_Doc(), ZS, parallel=4)
    assert [r["zone_id"] for r in out] == [z["id"] for z in ZS]


def test_parallel_reading_gives_the_same_result_as_sequential(monkeypatch):
    monkeypatch.setattr(ocr, "read_zone", _slow_read(0.01))
    a = ocr.read_all_zones(_Doc(), ZS, parallel=1)
    b = ocr.read_all_zones(_Doc(), ZS, parallel=4)
    assert a == b


def test_parallel_reading_is_actually_faster(monkeypatch):
    """ถ้าไม่เร็วขึ้นจริง แปลว่า pool ไม่ได้ทำงาน (เช่นถูก lock บังไว้)."""
    monkeypatch.setattr(ocr, "read_zone", _slow_read(0.06))
    t = time.time(); ocr.read_all_zones(_Doc(), ZS, parallel=1)
    seq = time.time() - t
    t = time.time(); ocr.read_all_zones(_Doc(), ZS, parallel=4)
    par = time.time() - t
    assert par < seq * 0.6, "ขนานแล้วไม่เร็วขึ้น (%.2f vs %.2f)" % (par, seq)


def test_one_zone_failing_never_breaks_the_batch(monkeypatch):
    def rz(doc, z, **kw):
        if z["id"] == "z3":
            return {"zone_id": "z3", "text": "", "engine": "none",
                    "rotate": 0, "error": "ต่อไม่ได้"}
        return {"zone_id": z["id"], "text": "ok", "engine": "n8n", "rotate": 0}
    monkeypatch.setattr(ocr, "read_zone", rz)
    out = ocr.read_all_zones(_Doc(), ZS, parallel=4)
    assert len(out) == len(ZS)
    assert next(r for r in out if r["zone_id"] == "z3")["error"]


def test_ignore_zones_are_still_skipped(monkeypatch):
    monkeypatch.setattr(ocr, "read_zone", _slow_read(0))
    zs = ZS + [{"id": "ig", "type": "ignore", "bbox": [0, 0, 1, 1]}]
    out = ocr.read_all_zones(_Doc(), zs, parallel=4)
    assert "ig" not in [r["zone_id"] for r in out]


def test_a_single_zone_never_spawns_a_pool(monkeypatch):
    monkeypatch.setattr(ocr, "read_zone", _slow_read(0))
    assert len(ocr.read_all_zones(_Doc(), ZS[:1], parallel=8)) == 1


def test_reading_both_sides_of_a_region_is_parallel_but_equal(monkeypatch):
    """โหมด pixel อ่านสองฝั่งพร้อมกัน — ผลต้องเท่าการอ่านเรียงกัน."""
    seen = []

    def ri(crop):
        time.sleep(0.05)
        seen.append(int(crop[0, 0, 0]))
        return {"text": "T%d" % int(crop[0, 0, 0])}
    monkeypatch.setattr(pipeline.ocr, "read_image", ri)
    ca = np.full((4, 4, 3), 7, np.uint8)
    cb = np.full((4, 4, 3), 9, np.uint8)
    t = time.time()
    a, b = pipeline._read_pair(ca, cb)
    el = time.time() - t
    assert (a, b) == ("T7", "T9")
    assert el < 0.09, "อ่านสองฝั่งไม่ได้ยิงพร้อมกัน (%.3f วินาที)" % el


# ══════════════════════════════════════════════════════════════════════
# ④ เรนเดอร์รอบเดียว — ความละเอียดที่ได้ต้องเท่าเดิมเป๊ะ
# ══════════════════════════════════════════════════════════════════════

def _panel_pdf(path, k=1.0, dv=20):
    import fitz
    doc = fitz.open()
    pg = doc.new_page(width=600, height=420)
    pg.draw_rect(fitz.Rect(120, 90, 120 + 210 * k, 90 + 130 * k),
                 color=(0, 0, 0), width=1)
    for i in range(6):
        pg.insert_text((128, 108 + i * 18 * k), "Sodium 475 mg %d%%" % dv,
                       fontsize=9 * k, fontname="cour")
    doc.save(str(path))
    doc.close()
    return str(path)


ZONE = [100 / 600.0, 70 / 420.0, 300 / 600.0, 180 / 420.0]


def test_the_dpi_is_decided_before_rendering_not_after(tmp_path):
    """คำนวณจาก mm ⇒ ไม่ต้องเรนเดอร์ทิ้งรอบแรก."""
    a = _panel_pdf(tmp_path / "a.pdf")
    got = PM._dpi_for(a, ZONE, a, ZONE, 220)
    px = PM._zone_px(a, ZONE, got)
    assert min(px) >= PM.MIN_SIDE_PX * 0.5
    assert max(px) >= PM.MIN_SIDE_PX * 0.9


def test_only_two_renders_happen_in_the_normal_case(tmp_path, monkeypatch):
    """เดิมเรนเดอร์ 4 ครั้ง (ทิ้ง 2) — ตอนนี้ต้องเหลือ 2."""
    a = _panel_pdf(tmp_path / "a.pdf")
    b = _panel_pdf(tmp_path / "b.pdf", dv=24)
    calls = []
    orig = pixdiff.render_zone_mm

    def spy(path, bbox, dpi, page=0, **kw):
        calls.append(dpi)
        return orig(path, bbox, dpi, page, **kw)
    monkeypatch.setattr(PM.pixdiff, "render_zone_mm", spy)
    PM.compare(a, ZONE, b, ZONE, dpi=220)
    assert len(calls) == 2, "เรนเดอร์ %d ครั้ง (ควรเป็น 2)" % len(calls)
    assert len(set(calls)) == 1, "สองฝั่งต้องเรนเดอร์ที่ dpi เดียวกัน"


def test_the_effective_dpi_is_unchanged_by_the_optimisation(tmp_path):
    """ต้องได้ความละเอียดเท่าเดิม — ห้ามแลกความแม่นเพื่อความเร็ว."""
    a = _panel_pdf(tmp_path / "a.pdf")
    b = _panel_pdf(tmp_path / "b.pdf", dv=24)
    r = PM.compare(a, ZONE, b, ZONE, dpi=220)
    assert r["dpi"] >= 220
    assert max(r["size"]) >= PM.MIN_SIDE_PX * 0.85


def test_a_non_pdf_page_falls_back_to_the_old_path(tmp_path):
    """อ่านขนาดหน้าไม่ได้ ⇒ ใช้ dpi ที่ขอมาตรง ๆ (แล้วค่อยเรนเดอร์ซ้ำถ้าเล็ก)."""
    assert PM._zone_px(str(tmp_path / "ไม่มีจริง.png"), ZONE, 400) is None
    assert PM._dpi_for(str(tmp_path / "x.png"), ZONE,
                       str(tmp_path / "y.png"), ZONE, 333) == 333
