# -*- coding: utf-8 -*-
"""F1 / F2 / F3 (25 ก.ย. 2026) — ลดรายการปลอมของคู่ vector ↔ raster หลายภาษา

ที่มา: คู่ Friskies Lil Lickables ขึ้น 5-7 รายการ เป็นของจริงแค่ 1 (CFPR)
  F1 เลขทะเบียน 12 หลักถูกนับเป็นบาร์โค้ด → info + เหตุผล
  F2 OCR อ่านเพี้ยน (``리커블`` → ``리커버``) พิสูจน์ได้ด้วย text layer ของไฟล์
     เอง → warning (REVIEW) + หลักฐาน
  F3 คำเดียวปนอักษรไทย+เกาหลี (``ผลิตภัณฑ์에``) → โน้ตเตือน

หลักที่เทสต์ล็อก: **ไม่มีรายการใดหาย** · ความต่างจริงต้องคง critical ·
"ไม่แน่ใจ" = ไม่แตะ · ปิดธง = เดิมทุกตัวอักษร
"""
import os
import re

import numpy as np
import pytest

from artwork_check import checks, config, ocr, report, witness as W

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = open(os.path.join(ROOT, "static", "js", "artwork_check.js"),
          encoding="utf-8").read()


@pytest.fixture(autouse=True)
def _flags_on(monkeypatch):
    monkeypatch.setattr(config, "NUMBER_CONTEXT", True)
    monkeypatch.setattr(config, "TEXT_WITNESS", True)
    monkeypatch.setattr(config, "TEXT_WITNESS_SEVERITY", "warning")
    monkeypatch.setattr(config, "FUSED_SCRIPT_NOTE", True)


# ── ตัวช่วย ───────────────────────────────────────────────────────────

def _pair_zones(n=2):
    zs = [{"id": "z1", "type": "panel", "group": "A", "doc": "a",
           "bbox": [0, 0, 1, 1]},
          {"id": "b2", "type": "panel", "group": "A", "doc": "b",
           "bbox": [0, 0, 1, 1]}]
    if n == 3:
        zs.append({"id": "z3", "type": "panel", "group": "A", "doc": "a",
                   "bbox": [0, 0, 1, 1]})
    return zs


def _run(texts, wit=None, engines=None, n=2):
    ocr_results = []
    for zid, t in texts.items():
        r = {"zone_id": zid, "text": t, "conf": 0.96,
             "engine": (engines or {}).get(zid, "n8n")}
        if wit and zid in wit:
            r["witness"] = wit[zid]
        ocr_results.append(r)
    return checks.run_all_checks(_pair_zones(n), ocr_results)


def _mismatch(ds):
    return [d for d in ds if d["class"] == "MISMATCH_PANELS"]


KOR_OK = "제품명: 프리스키 리커블 흰살생선\nNet weight 56 g"
KOR_BAD = "제품명: 프리스키 리커버 흰살생선\nNet weight 56 g"
# text layer ของเกาหลีจริง: ไม่มีช่องว่าง — ช่องว่างถูกแมปเป็น U+0001
WIT_KOR = "제품명:\x01프리스키\x01리커블\x01흰살생선\nNet weight 56 g"
WIT_KOR_REAL = "제품명:\x01프리스키\x01리커버\x01흰살생선\nNet weight 56 g"


# ══ F1 ═══════════════════════════════════════════════════════════════

BAD12 = "200201013615"            # check digit ผิดตามสูตร GS1 (เลขทะเบียนจริง)


def test_fixture_number_really_fails_the_check_digit():
    assert not checks.gs1_check_digit_ok(BAD12)


def _num(text):
    return [d for d in checks.check_numbers(
        [{"id": "z1", "type": "panel"}], {"z1": text})
        if d["class"] == "NUMBER_FAIL"]


@pytest.mark.parametrize("text,why", [
    ("Nestlé Products Sdn. Bhd. (%s)" % BAD12, "วงเล็บ"),
    ("CFPR No. SF-CFI2-26-%s" % BAD12, "ขีด"),
    ("%s-A1 lot code" % BAD12, "ขีด"),
    ("Reg. No.: %s" % BAD12, "ทะเบียน"),
    ("Tel: %s" % BAD12, "โทร"),
    ("등록번호 %s" % BAD12, "ทะเบียน"),
])
def test_obvious_non_barcodes_become_info_with_reason(text, why):
    d = _num(text)
    assert len(d) == 1, "ห้ามลบรายการ — แค่ลดระดับ"
    assert d[0]["severity"] == "info"
    assert why in d[0]["why"]


@pytest.mark.parametrize("text", [
    BAD12,                                        # เลขเดี่ยวบนบรรทัด
    "> %s" % BAD12,                               # บาร์โค้ดที่มีเครื่องหมาย
    "EAN (%s)" % BAD12,                           # มีคำ EAN ⇒ ตรวจเสมอ
    "GTIN No. %s" % BAD12,                        # หลัง No. แต่มีคำ GTIN
    "Keep dry %s tuna in water" % BAD12,          # OCR อ่านปนข้อความ
    "บาร์โค้ด (%s)" % BAD12,
])
def test_anything_that_may_be_a_barcode_stays_critical(text):
    d = _num(text)
    assert len(d) == 1
    assert d[0]["severity"] == "critical"
    assert "why" not in d[0]


def test_context_is_read_from_the_numbers_own_line_only():
    """คำว่า Reg บนบรรทัดก่อนหน้า ต้องไม่ทำให้บาร์โค้ดบรรทัดถัดไปถูกข้าม"""
    d = _num("Reg. No.\n%s" % BAD12)
    assert d[0]["severity"] == "critical"


def test_number_context_flag_off_is_byte_identical(monkeypatch):
    text = "Nestlé Products Sdn. Bhd. (%s)" % BAD12
    on = _num(text)
    monkeypatch.setattr(config, "NUMBER_CONTEXT", False)
    off = _num(text)
    assert off[0]["severity"] == "critical" and "why" not in off[0]
    strip = lambda d: {k: v for k, v in d.items() if k not in ("severity", "why")}
    assert strip(on[0]) == strip(off[0])
    assert set(off[0]) == set(strip(off[0])) | {"severity"}


def test_valid_barcodes_are_still_silent():
    body = "885012400387"
    good = next(body + str(c) for c in range(10)
                if checks.gs1_check_digit_ok(body + str(c)))
    assert _num(good) == []
    assert _num("Reg. No. " + good) == []


# ══ F2 ═══════════════════════════════════════════════════════════════

def test_ocr_misread_proven_by_text_layer_becomes_review_with_evidence():
    ds = _run({"z1": KOR_OK, "b2": KOR_BAD}, wit={"b2": WIT_KOR})
    m = _mismatch(ds)
    assert len(m) == 1, "ห้ามลบรายการ"
    assert m[0]["severity"] == "warning"
    ev = m[0]["witness"]
    assert ev["zone"] == "b2" and ev["kind"] == "pair"
    assert ev["ocr_says"] == ["리커버"]
    assert ev["file_diff"] == ["리커블"]
    assert "리커블" in ev["file_says"]
    assert report.compute_verdict(ds) == "REVIEW"


def test_real_difference_stays_critical():
    """OCR ของฝั่งที่มีพยานอ่านตรงกับไฟล์ ⇒ ความต่างมีอยู่จริง"""
    ds = _run({"z1": KOR_OK, "b2": KOR_BAD}, wit={"b2": WIT_KOR_REAL})
    m = _mismatch(ds)
    assert m and all(d["severity"] == "critical" for d in m)
    assert all("witness" not in d for d in m)


def test_both_sides_misread_is_not_downgraded():
    """พยานพิสูจน์ได้แค่ฝั่งตัวเอง — อีกฝั่งก็ผิด ⇒ ตัดสินไม่ได้ ⇒ ไม่แตะ"""
    ds = _run({"z1": KOR_OK.replace("리커블", "리커브"), "b2": KOR_BAD},
              wit={"b2": WIT_KOR})
    assert all(d["severity"] == "critical" for d in _mismatch(ds))


def test_punctuation_is_kept_so_a_real_placeholder_is_not_hidden():
    """รุ่นแรกของต้นแบบเทียบด้วย ``_norm_key`` (ตัดวรรคตอน) ⇒ ``Nox`` ไปเจอใน
    ``No. xxxxxx`` แล้วซ่อน CFPR ที่ต่างจริง"""
    ds = _run({"z1": "CFPR No. SF-CF12-26\nNet weight 56 g",
               "b2": "CFPR Nox xxxxxx\nNet weight 56 g"},
              wit={"b2": "CFPR No. xxxxxx\nNet weight 56 g"})
    m = _mismatch(ds)
    assert m and all(d["severity"] == "critical" for d in m)


def test_replace_at_the_line_edge_is_a_real_difference():
    """ต่างที่คำแรก/คำสุดท้าย ต้องไม่ถูกยกโทษว่าเป็นการตัดบรรทัด"""
    w = "Net weight 56 g per pack"
    assert W.agree(w, "Net weight 56 g per pack", w, "") is True
    assert W.agree(w, "Net weight 56 g per box", w, "") is False
    assert W.agree(w, "Gross weight 56 g per pack", w, "") is False


def test_edge_difference_that_is_only_a_rewrap_is_allowed():
    w = "Net weight 56 g per pack"
    # อีกฝั่งตัดบรรทัดก่อน "per pack" แล้วไปพิมพ์ต่อบรรทัดถัดไป
    assert W.agree(w, "Net weight 56 g", w,
                   "Net weight 56 g\nper pack") is True
    # ส่วนเกินไม่มีอยู่ที่ไหนในแผงอีกฝั่ง = ต่างจริง
    assert W.agree(w, "Net weight 56 g", w, "Net weight 56 g") is False


def test_no_witness_returns_the_same_list_object():
    ds = [{"class": "MISMATCH_PANELS", "zone_id": "z1", "found": "a",
           "reference": "b", "severity": "critical"}]
    out = W.apply_witness(ds, _pair_zones(), [{"zone_id": "z1", "text": "a"}],
                          {"z1": "a"})
    assert out is ds


def test_groups_larger_than_two_panels_are_not_touched():
    texts = {"z1": KOR_OK, "b2": KOR_BAD, "z3": KOR_OK}
    ds = _run(texts, wit={"b2": WIT_KOR}, n=3)
    assert all("witness" not in d for d in ds)
    # ด่าน ``_pairs`` เอง: แม้การ์ดจะอ้างโซนเดียว กลุ่ม 3 panel ก็ต้องไม่ถูกแตะ
    card = {"class": "MISMATCH_PANELS", "zone_id": "b2", "found": "리커버 흰살생선",
            "reference": "리커블 흰살생선", "ref_zone_ids": ["z1"],
            "severity": "critical"}
    out = W.apply_witness([card], _pair_zones(3),
                          [{"zone_id": "b2", "witness": WIT_KOR}], texts)
    assert out[0]["severity"] == "critical" and "witness" not in out[0]
    # เทียบ: กลุ่ม 2 panel การ์ดเดียวกันถูกลดระดับ (เทสต์ข้างบนจึงมีความหมาย)
    out2 = W.apply_witness([card], _pair_zones(2),
                           [{"zone_id": "b2", "witness": WIT_KOR}], texts)
    assert out2[0]["severity"] == "warning"


def test_only_mismatch_panels_are_judged():
    ds = [{"class": "MISMATCH_CASE", "zone_id": "z1", "found": KOR_OK,
           "reference": KOR_BAD, "ref_zone_ids": ["b2"],
           "severity": "critical"}]
    out = W.apply_witness(ds, _pair_zones(),
                          [{"zone_id": "b2", "witness": WIT_KOR}],
                          {"z1": KOR_OK, "b2": KOR_BAD})
    assert out[0]["severity"] == "critical"


def test_found_only_card_extra_there():
    """การ์ด "พบเฉพาะใน z1" แต่ไฟล์ของ b2 ก็พิมพ์บรรทัดนั้นจริง"""
    d = {"class": "MISMATCH_PANELS", "zone_id": "z1",
         "found": "제품에 별도 표시 (일월년 순)", "reference": ""}
    ev = W.judge(d, {"b2": W.witness_lines("제품에\x01별도\x01표시\x01(일월년\x01순)")},
                 {"z1": "", "b2": "ผลิตภัณฑ์에 별도 표시"}, "b2")
    assert ev and ev["kind"] == "extra_there" and ev["zone"] == "b2"


def test_found_only_card_that_is_really_extra_is_not_touched():
    d = {"class": "MISMATCH_PANELS", "zone_id": "z1",
         "found": "NEW CLAIM 100% natural", "reference": ""}
    ev = W.judge(d, {"b2": W.witness_lines("Net weight 56 g")},
                 {"z1": "NEW CLAIM 100% natural", "b2": "Net weight 56 g"},
                 "b2")
    assert ev is None


def test_found_only_card_extra_here():
    """ไฟล์ของ b2 พิมพ์บรรทัดที่ z1 มีอยู่แล้ว แต่ OCR ของ b2 อ่านเป็นอย่างอื่น"""
    d = {"class": "MISMATCH_PANELS", "zone_id": "b2",
         "found": "ผลิตภัณฑ์에 별도 표시(일월년 순) 중량", "reference": ""}
    wl = W.witness_lines("제품에\x01별도\x01표시(일월년\x01순)\x01중량")
    ev = W.judge(d, {"b2": wl},
                 {"b2": d["found"], "z1": "제품에 별도 표시 (일월년 순) 중량"},
                 "z1")
    assert ev and ev["kind"] == "extra_here"


def test_bad_glyphs_become_separators_that_never_match():
    wl = W.witness_lines("AB\x01CD")
    assert wl == ["AB %s CD" % W.SEP]
    assert W.flatp(wl[0]) == "abcd"
    assert W._show(wl[0]) == "AB▯CD"


def test_witness_severity_is_configurable(monkeypatch):
    monkeypatch.setattr(config, "TEXT_WITNESS_SEVERITY", "info")
    ds = _run({"z1": KOR_OK, "b2": KOR_BAD}, wit={"b2": WIT_KOR})
    assert _mismatch(ds)[0]["severity"] == "info"


def test_witness_flag_off_is_identical(monkeypatch):
    on = _run({"z1": KOR_OK, "b2": KOR_BAD}, wit={"b2": WIT_KOR})
    monkeypatch.setattr(config, "TEXT_WITNESS", False)
    monkeypatch.setattr(config, "FUSED_SCRIPT_NOTE", False)
    off = _run({"z1": KOR_OK, "b2": KOR_BAD}, wit={"b2": WIT_KOR})
    assert len(on) == len(off)
    assert all(d["severity"] == "critical" and "witness" not in d
               for d in _mismatch(off))


# ── read_zone เก็บพยานเฉพาะเมื่อ text layer ถูกปฏิเสธ ─────────────────

class _Doc(object):
    is_pdf = True

    def __init__(self, embedded):
        self.embedded = embedded

    def embedded_text(self, bbox=None):
        return self.embedded

    def render_zone(self, bbox, dpi, max_side=None):
        return np.full((1400, 1400, 3), 255, np.uint8)


ZONE = {"id": "b2", "type": "panel", "bbox": [0, 0, 1, 1], "rotate": 0}


@pytest.fixture
def ocr_on(monkeypatch):
    monkeypatch.setattr(ocr.vertex_client, "is_enabled", lambda: True)
    monkeypatch.setattr(ocr.vertex_client, "ocr_image",
                        lambda b: {"text": "ผลิตภัณฑ์에 별도 표시",
                                   "blocks": [], "engine": "mock"})
    monkeypatch.setattr(config, "PDFTEXT_BAD_GLYPH_CHECK", True)


def test_rejected_text_layer_is_kept_as_witness(ocr_on):
    r = ocr.read_zone(_Doc(WIT_KOR), ZONE)
    assert r["engine"] == "mock", "ต้องไม่ใช้ text layer ที่เสียเป็นข้อความ"
    assert r["witness"] == WIT_KOR
    assert r["text"] == "ผลิตภัณฑ์에 별도 표시"


def test_clean_text_layer_has_no_witness_key(ocr_on):
    r = ocr.read_zone(_Doc("Net weight 56 g · Ingredients: water, tuna"), ZONE)
    assert r["engine"] == "pdf-text"
    assert "witness" not in r


def test_witness_flag_off_does_not_store_it(ocr_on, monkeypatch):
    monkeypatch.setattr(config, "TEXT_WITNESS", False)
    r = ocr.read_zone(_Doc(WIT_KOR), ZONE)
    assert "witness" not in r


# ══ F3 ═══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("text,toks", [
    ("ผลิตภัณฑ์에 별도 표시", ["ผลิตภัณฑ์에"]),
    ("表示(วันเดือนปี) ok", ["表示(วันเดือนปี)"]),
    ("ของجم", ["ของجم"]),
    ("カツオไทย", ["カツオไทย"]),
])
def test_fused_tokens_found(text, toks):
    assert W.fused_tokens(text) == toks


@pytest.mark.parametrize("text", [
    "ผลิตภัณฑ์ 에 별도",        # มีช่องว่าง = ปกติ
    "น้ำหนักสุทธิ56g",          # ไทย+ละติน/ตัวเลข ไม่นับ
    "Tuna100%",
    "제품명 프리스키",
    "مكونات الماء",
    "",
])
def test_fused_tokens_not_found(text):
    assert W.fused_tokens(text) == []


def test_fused_note_on_ocr_zone(ocr_on):
    r = ocr.read_zone(_Doc(WIT_KOR), ZONE)
    assert "ผลิตภัณฑ์에" in r["note"] and "เกาหลี" in r["note"]


def test_fused_note_flag_off(ocr_on, monkeypatch):
    monkeypatch.setattr(config, "FUSED_SCRIPT_NOTE", False)
    r = ocr.read_zone(_Doc(WIT_KOR), ZONE)
    assert "ผลิตภัณฑ์에" not in (r.get("note") or "")


def test_card_is_marked_but_severity_untouched():
    ds = _run({"z1": "ผลิตภัณฑ์에 별도 표시\nNet 56 g",
               "b2": "제품에 별도 표시\nNet 56 g"})
    m = _mismatch(ds)
    assert m and any(d.get("fused") == ["ผลิตภัณฑ์에"] for d in m)
    assert all(d["severity"] == "critical" for d in m)


def test_text_layer_side_is_never_marked():
    """text layer เกาหลีไม่มีช่องว่าง ⇒ ``กรัม제품명`` ติดกันเป็นเรื่องปกติ"""
    ds = _run({"z1": "กรัม제품명 별도\nNet 56 g", "b2": "กรัม 제품 별도\nNet 56 g"},
              engines={"z1": "pdf-text"})
    assert all("fused" not in d for d in ds)


# ══ ฝั่งหน้าจอ ═══════════════════════════════════════════════════════

def test_card_renders_every_new_field():
    assert "if (d.why) {" in JS
    assert "if (d.witness && d.witness.file_says) {" in JS
    assert "if (d.fused && d.fused.length) {" in JS
    assert "w.kind === \"pair\"" in JS and "w.kind === \"extra_here\"" in JS


def test_card_escapes_evidence_text():
    seg = JS[JS.index("if (d.witness && d.witness.file_says)"):]
    seg = seg[:seg.index("if (d.fused")]
    assert "esc(w.file_says)" in seg
    assert re.search(r"\+ w\.file_says|\+ w\.zone\b", seg) is None


def test_defaults_are_on():
    src = open(os.path.join(ROOT, "artwork_check", "config.py"),
               encoding="utf-8").read()
    for k in ("ARTWORK_NUMBER_CONTEXT", "ARTWORK_TEXT_WITNESS",
              "ARTWORK_FUSED_SCRIPT_NOTE"):
        assert re.search(r'"%s", "1"\)' % k, src), k
    assert '"ARTWORK_TEXT_WITNESS_SEVERITY", "warning"' in src


def test_punctuation_difference_is_a_real_difference():
    """``_norm_key`` ตัดวรรคตอน ⇒ ``5.5%`` กับ ``55%`` จะกลายเป็นตัวเดียวกัน"""
    w = "Crude Protein 5.5% (Min)"
    assert W.agree(w, "Crude Protein 55% (Min)", w, "") is False
    assert W.agree(w, "Crude Protein 5.5% (Min)", w, "") is True
