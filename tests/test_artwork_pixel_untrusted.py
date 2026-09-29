# -*- coding: utf-8 -*-
"""P5 (26 ก.ย. 2026) — โหมด pixel กับผลที่ "ไม่น่าเชื่อถือ"

ที่มา (คู่ Friskies vector ↔ raster บนสถานี): ชั้นภาพพบ 420 บริเวณ ต่าง 33%
⇒ ``_pixel_untrusted`` กันไว้ถูกต้อง **แต่**
  ① อ่านข้อความทีละบริเวณครบก่อนทิ้ง — 150 วิแทน 20 วิ + เผาโควตา
  ② หน้าจอบอกว่า "เทียบแล้ว **ไม่พบความต่าง**" (ยุบรวมกับเคสพบ 0)
  ③ คำแนะนำ "ลากครอบนอกแผง" ผิดสาเหตุ — คู่ vector↔raster ต่างทั้งแผง
     โดยโครงสร้าง ลากใหม่ไม่ช่วย
"""
import os

import fitz
import numpy as np
import pytest

from artwork_check import config, pipeline, pixdiff
from artwork_check import progress as PG
from artwork_check.pdf_ingest import ArtworkDocument

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = open(os.path.join(ROOT, "static", "js", "artwork_check.js"),
          encoding="utf-8").read()
IMG = np.zeros((400, 400, 3), dtype=np.uint8)
ZONES = [{"id": "z1", "type": "panel", "group": "A", "bbox": [0, 0, 1, 1],
          "doc": "a"},
         {"id": "z2", "type": "panel", "group": "A", "bbox": [0, 0, 1, 1],
          "doc": "b"}]
TEXT_DEFECT = {"class": "MISMATCH_PANELS", "zone_id": "z1", "found": "24%"}


def _vector_pdf(path):
    d = fitz.open()
    p = d.new_page(width=300, height=200)
    p.insert_text((20, 60), "Net weight 56 g", fontsize=14)
    d.save(str(path))
    return str(path)


def _raster_pdf(path, dpi=300):
    """หน้า = ภาพเดียวครอบทั้งหน้า (แบบปรู๊ฟที่ส่งออกเป็นภาพ)"""
    w_pt, h_pt = 300, 200
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, int(w_pt * dpi / 72),
                                              int(h_pt * dpi / 72)), False)
    pix.set_rect(pix.irect, (255, 255, 255))
    d = fitz.open()
    p = d.new_page(width=w_pt, height=h_pt)
    p.insert_image(p.rect, pixmap=pix)
    d.save(str(path))
    return str(path)


def _res(dr, ecc):
    return {"status": pixdiff.OK, "edge_regions": 0, "scale": 1.0,
            "ncc": 1.0, "ecc": ecc, "diff_ratio": dr, "size": [400, 400],
            "dpi": 400, "mm_per_px": 0.0635, "min_region_mm2": 0.06,
            "areas_mm2": [0.25] * 3,
            "regions": [{"px": [10, 10, 20, 20], "px_a": [10, 10, 20, 20],
                         "bbox": [0.02, 0.02, 0.05, 0.05], "area_px": 61}] * 3}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    src = _vector_pdf(tmp_path / "a.pdf")
    monkeypatch.setattr(pipeline, "_find_source", lambda d, *a, **k: src)
    monkeypatch.setattr(config, "PIXEL_TRUST_GATE", True)
    monkeypatch.setattr(config, "PIXEL_SKIP_OCR_WHEN_UNTRUSTED", True)
    monkeypatch.setattr(config, "PIXEL_RASTER_HINT", True)
    calls = []

    def _r2d(*a, **k):
        calls.append(1)
        return [{"class": "MISMATCH_PANELS", "zone_id": "z1", "found": "x"}]

    monkeypatch.setattr(pipeline.panelmatch_mod, "regions_to_defects", _r2d)
    return tmp_path, calls


def test_untrusted_result_is_not_read_region_by_region(setup, monkeypatch):
    tmp, calls = setup
    monkeypatch.setattr(pipeline.panelmatch_mod, "compare_ex",
                        lambda *a, **k: (_res(0.33, 0.52), IMG, IMG))
    out, info = pipeline._pixel_compare(str(tmp), ZONES, [TEXT_DEFECT])
    assert calls == [], "อ่านข้อความทีละบริเวณทั้งที่ผลจะถูกทิ้งอยู่แล้ว"
    assert out == [TEXT_DEFECT]
    p = info["pairs"][0]
    assert p["kept_text_layer"] and p["untrusted"] and p["ocr_skipped"]


def test_flag_off_reads_then_discards_like_before(setup, monkeypatch):
    tmp, calls = setup
    monkeypatch.setattr(config, "PIXEL_SKIP_OCR_WHEN_UNTRUSTED", False)
    monkeypatch.setattr(pipeline.panelmatch_mod, "compare_ex",
                        lambda *a, **k: (_res(0.33, 0.52), IMG, IMG))
    out, info = pipeline._pixel_compare(str(tmp), ZONES, [TEXT_DEFECT])
    assert calls == [1]
    assert out == [TEXT_DEFECT]                  # ผลเท่าเดิมทุกไบต์
    assert info["pairs"][0]["untrusted"] and "ocr_skipped" not in info["pairs"][0]


def test_trusted_result_still_replaces(setup, monkeypatch):
    tmp, calls = setup
    monkeypatch.setattr(pipeline.panelmatch_mod, "compare_ex",
                        lambda *a, **k: (_res(0.0001, 0.998), IMG, IMG))
    out, info = pipeline._pixel_compare(str(tmp), ZONES, [TEXT_DEFECT])
    assert calls == [1] and info["used"] == 1
    assert out == [{"class": "MISMATCH_PANELS", "zone_id": "z1", "found": "x"}]


def test_progress_does_not_call_untrusted_results_no_difference():
    run = PG.begin("p5")
    pipeline._report_pixel_progress(run, {"used": 0, "pairs": [
        {"group": "A", "status": "ok", "regions": 420,
         "kept_text_layer": True, "untrusted": "ต่างกัน 33.00%"},
        {"group": "B", "status": "ok", "regions": 0, "kept_text_layer": True},
    ]})
    s = next(x for x in PG.snapshot("p5")["steps"] if x["key"] == "pixel")
    a = next(n for n in s["notes"] if "กลุ่ม A" in n)
    b = next(n for n in s["notes"] if "กลุ่ม B" in n)
    assert "ไม่พบความต่าง" not in a and "ยังไม่น่าเชื่อถือ" in a and "420" in a
    assert "ไม่พบความต่าง" in b


def test_raster_page_reads_dpi_from_metadata(tmp_path):
    assert ArtworkDocument(_raster_pdf(tmp_path / "r.pdf", 300)).raster_page() == 300
    assert ArtworkDocument(_raster_pdf(tmp_path / "q.pdf", 400)).raster_page() == 400
    assert ArtworkDocument(_vector_pdf(tmp_path / "v.pdf")).raster_page() is None


def test_raster_keys_follow_the_flag(setup, monkeypatch):
    tmp, _ = setup
    monkeypatch.setattr(pipeline.panelmatch_mod, "compare_ex",
                        lambda *a, **k: (_res(0.33, 0.52), IMG, IMG))
    _, info = pipeline._pixel_compare(str(tmp), ZONES, [TEXT_DEFECT])
    assert "raster_a" in info["pairs"][0] and info["pairs"][0]["raster_a"] is None
    monkeypatch.setattr(config, "PIXEL_RASTER_HINT", False)
    _, info = pipeline._pixel_compare(str(tmp), ZONES, [TEXT_DEFECT])
    assert "raster_a" not in info["pairs"][0]


def test_js_separates_untrusted_from_no_difference():
    i = JS.index("p.kept_text_layer && p.untrusted")
    j = JS.index("เทียบด้วยภาพแล้ว <b>ไม่พบความต่าง</b>")
    assert i < j, "สาขา untrusted ต้องมาก่อนสาขา 'ไม่พบความต่าง'"
    assert "ผลจากภาพยังไม่น่าเชื่อถือ" in JS


def test_js_advice_names_the_vector_raster_cause():
    seg = JS[JS.index("const mixed = "):JS.index("if (Array.isArray(p.ocr_capped)")]
    assert "(rA != null || rB != null) && rA !== rB" in seg
    assert "ลากโซนใหม่ไม่ช่วย" in seg
    assert "น่าจะลากครอบเนื้อหานอกแผงเข้ามา" in seg   # เคสเดิมยังพูดแบบเดิม
