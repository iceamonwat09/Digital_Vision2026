# -*- coding: utf-8 -*-
"""P6 (26 ก.ย. 2026, โหมดทดลอง ปิดเป็นค่าเริ่มต้น) — อ่านซ้ำเฉพาะบรรทัดที่ต่าง

กติกาที่ล็อก: **ไม่แตะระดับ ไม่ลบการ์ด** · หาตำแหน่งไม่เจอ/กำกวม = ไม่อ่าน
· ภาพต้องขนาดเท่ากับที่ OCR เห็น · ฝั่งที่มีพยาน/อ่านจาก text layer ไม่อ่าน
· เพดานจำนวนครั้ง · ปิดธง = รายการเดิม (ตัวเดียวกัน)
"""
import os

import fitz
import pytest

from artwork_check import config, ocr as ocr_mod, reread as RR
from artwork_check.pdf_ingest import ArtworkDocument

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = open(os.path.join(ROOT, "static", "js", "artwork_check.js"),
          encoding="utf-8").read()
PIPE = open(os.path.join(ROOT, "artwork_check", "pipeline.py"),
            encoding="utf-8").read()

LINE_A = "Net weight 56 g per pack"
LINE_B = "Net weight 58 g per pack"


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(config, "LINE_REREAD", True)
    monkeypatch.setattr(config, "LINE_REREAD_MAX", 6)


def _pdf(path):
    d = fitz.open()
    p = d.new_page(width=300, height=200)
    p.insert_text((20, 60), "Ingredients: tuna", fontsize=12)
    p.insert_text((20, 100), LINE_A, fontsize=12)
    d.save(str(path))
    return str(path)


def _setup(tmp_path, engine_b="n8n", witness_b=None):
    doc = ArtworkDocument(_pdf(tmp_path / "a.pdf"))
    zones = [{"id": "z1", "type": "panel", "group": "A", "doc": "a",
              "bbox": [0, 0, 1, 1]},
             {"id": "b2", "type": "panel", "group": "A", "doc": "b",
              "bbox": [0, 0, 1, 1]}]
    crop = ocr_mod._render_for_ocr(doc, [0, 0, 1, 1])
    wh = [int(crop.shape[1]), int(crop.shape[0])]
    blocks = [{"text": "Ingredients: tuna", "bbox": [0.05, 0.22, 0.4, 0.07]},
              {"text": LINE_A, "bbox": [0.05, 0.42, 0.6, 0.07]}]
    ocr = [{"zone_id": "z1", "engine": "n8n", "text": LINE_A,
            "blocks": blocks, "ocr_wh": wh, "rotate": 0},
           {"zone_id": "b2", "engine": engine_b, "text": LINE_B,
            "blocks": [dict(b, text=b["text"].replace("56", "58"))
                       for b in blocks], "ocr_wh": wh, "rotate": 0}]
    if witness_b:
        ocr[1]["witness"] = witness_b
    card = {"class": "MISMATCH_PANELS", "zone_id": "z1", "severity": "critical",
            "found": LINE_A, "reference": LINE_B, "ref_zone_ids": ["b2"]}
    return zones, ocr, card, {"a": doc, "b": doc}


def test_flag_off_returns_the_same_list(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LINE_REREAD", False)
    zones, ocr, card, docs = _setup(tmp_path)
    ds = [card]
    assert RR.apply(ds, zones, ocr, docs) is ds


def test_default_is_off():
    src = open(os.path.join(ROOT, "artwork_check", "config.py"),
               encoding="utf-8").read()
    assert '"ARTWORK_LINE_REREAD", "0"' in src


def test_reread_is_attached_without_touching_severity(tmp_path, on,
                                                      monkeypatch):
    zones, ocr, card, docs = _setup(tmp_path)
    seen = []

    def _read(crop):
        seen.append(crop.shape)
        return {"text": LINE_B, "engine": "n8n"}          # อ่านซ้ำได้แบบอีกฝั่ง

    monkeypatch.setattr(ocr_mod, "read_image", _read)
    out = RR.apply([card], zones, ocr, docs)
    assert out[0]["severity"] == "critical"
    assert out[0]["found"] == LINE_A and out[0]["reference"] == LINE_B
    rr = out[0]["reread"]
    assert [r["zone"] for r in rr] == ["z1", "b2"]
    assert rr[0]["verdict"] == "matches_other"
    # ครอปเฉพาะบรรทัด — เล็กกว่าทั้งโซนมาก
    full = ocr[0]["ocr_wh"]
    assert all(h < full[1] * 0.4 for h, w, _ in seen)


def test_pdf_text_and_witnessed_sides_are_not_reread(tmp_path, on,
                                                     monkeypatch):
    monkeypatch.setattr(ocr_mod, "read_image",
                        lambda c: {"text": LINE_A, "engine": "n8n"})
    zones, ocr, card, docs = _setup(tmp_path, engine_b="pdf-text")
    out = RR.apply([card], zones, ocr, docs)
    assert [r["zone"] for r in out[0]["reread"]] == ["z1"]
    zones, ocr, card, docs = _setup(tmp_path, witness_b=LINE_B)
    out = RR.apply([card], zones, ocr, docs)
    assert [r["zone"] for r in out[0]["reread"]] == ["z1"]


def test_only_critical_pair_cards_are_reread(tmp_path, on, monkeypatch):
    monkeypatch.setattr(ocr_mod, "read_image",
                        lambda c: {"text": LINE_A, "engine": "n8n"})
    zones, ocr, card, docs = _setup(tmp_path)
    warn = dict(card, severity="warning")
    only = dict(card, reference="")
    out = RR.apply([warn, only], zones, ocr, docs)
    assert all("reread" not in d for d in out)


def test_budget_caps_the_extra_ocr_calls(tmp_path, on, monkeypatch):
    monkeypatch.setattr(config, "LINE_REREAD_MAX", 1)
    n = []
    monkeypatch.setattr(ocr_mod, "read_image",
                        lambda c: n.append(1) or {"text": "", "engine": "n8n"})
    zones, ocr, card, docs = _setup(tmp_path)
    RR.apply([card, dict(card)], zones, ocr, docs)
    assert len(n) == 1


def test_image_size_mismatch_means_no_read(tmp_path, on, monkeypatch):
    monkeypatch.setattr(ocr_mod, "read_image",
                        lambda c: pytest.fail("อ่านภาพที่พิกัดไม่ตรง"))
    zones, ocr, card, docs = _setup(tmp_path)
    for r in ocr:
        r["ocr_wh"] = [r["ocr_wh"][0] + 7, r["ocr_wh"][1]]
    out = RR.apply([card], zones, ocr, docs)
    assert "reread" not in out[0]


def test_line_box_refuses_ambiguous_or_spread_matches():
    bl = [{"text": LINE_A, "bbox": [0, 0.1, 0.5, 0.05]},
          {"text": LINE_A, "bbox": [0, 0.6, 0.5, 0.05]}]
    assert RR.line_box(LINE_A, bl, [1000, 1000]) is None        # สองที่ = กำกวม
    words = [{"text": "Net weight", "bbox": [0, 0.1, 0.2, 0.05]},
             {"text": "per pack", "bbox": [0.3, 0.9, 0.2, 0.05]}]
    assert RR.line_box(LINE_A, words, [1000, 1000]) is None     # กระจายหลายแถว
    row = [{"text": "Net weight", "bbox": [0, 0.1, 0.2, 0.05]},
           {"text": "56 g per", "bbox": [0.2, 0.1, 0.2, 0.05]},
           {"text": "pack", "bbox": [0.4, 0.1, 0.1, 0.05]}]
    assert RR.line_box(LINE_A, row, [1000, 1000]) is not None
    assert RR.line_box(LINE_A, [], [1000, 1000]) is None


def test_judge_reads_the_differing_spans():
    assert RR.judge(LINE_B, LINE_A, LINE_B) == "matches_other"
    assert RR.judge(LINE_A, LINE_A, LINE_B) == "same"
    assert RR.judge("Net weight 5 g", LINE_A, LINE_B) == "unclear"
    assert RR.judge("", LINE_A, LINE_B) == "unclear"


def test_pipeline_calls_it_only_behind_the_flag():
    i = PIPE.index("reread_mod.apply(")
    assert "if config.LINE_REREAD" in PIPE[i - 400:i]


def test_js_says_it_does_not_change_the_result():
    seg = JS[JS.index("Array.isArray(d.reread)"):]
    seg = seg[:seg.index("});")]
    assert "ไม่ได้เปลี่ยนผลตรวจ" in seg and "esc(r.text" in seg
