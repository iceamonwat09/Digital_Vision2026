"""
หลักฐานระดับ **ฟอนต์**: "พังครั้งหนึ่ง = สงสัยทั้งไฟล์" (`artwork_check/fonttrust.py`)

ที่มาของชั้นนี้ — วัดจากไฟล์จริง (Cosma Schlemmerbox ฉบับ A4):

  * ความเสียหายเกาะอยู่กับฟอนต์เดียว: `DINPro-Bold` เสีย 66/396 span ส่วนอีก
    12 ฟอนต์ในไฟล์เดียวกัน **0%**
  * ฟอนต์ที่พังตัวเดียวกันคายขยะได้ 2 แบบ และแบบที่สองตรวจจากตัวข้อความไม่ได้:

        [span 0] 'DOPLNKOVÉ KRMIVO PRE DOSPELÉ MA'   ← อักขระต้องห้าม 0
        [span 1] 'Ï/='                                ← อักขระต้องห้าม 0
                  (ของจริงคือ "ČKY")

ชั้นนี้จึงตัดสินจาก "ฟอนต์ไหนถูกพิสูจน์แล้วว่าพัง" ไม่ใช่จากหน้าตาของข้อความ
ก้อนนั้น. **ทำงานเฉพาะเมื่อมีหลักฐานแข็งแล้วเท่านั้น** ⇒ ไฟล์ที่ฟอนต์ปกติ
ทั้งหมดต้องไม่ถูกแตะแม้แต่โซนเดียว.
"""

import numpy as np
import pytest

from artwork_check import config, fonttrust, ocr, pipeline


# ── span จริงที่ดึงมาจากไฟล์ที่ผู้ใช้ส่งมา ────────────────────────────
BROKEN_FONT = "DINPro-Bold"
CLEAN_FONT = "DINPro-Medium"

# span ที่ "มีหลักฐานแข็ง" (อักขระควบคุม) — ตัวพิสูจน์ว่าฟอนต์นี้พัง
HARD_EVIDENCE = "PEGMJMGOÀ\x04XYċ¤O\x04Z\x04ŁIlé. SLOŽENÍ"
# span ที่เป็นขยะ **แต่ไม่มีอักขระต้องห้ามเลย** — ด่านรายโซนจับไม่ได้
SILENT_GARBAGE = "Ï/="
# span ของฟอนต์เดียวกันที่ถูกต้องจริง ๆ
GOOD_SLOVAK = "DOPLNKOVÉ KRMIVO PRE DOSPELÉ MA"
GOOD_ENGLISH = "Pacific tuna in jelly."
# ข้อความจากฟอนต์ที่ไม่มีร่องรอยเสียหาย (ใช้เป็นฐานเทียบ "อักขระที่งานนี้ใช้จริง")
# ⚠️ ต้องมีอักขระที่ "งานพิมพ์ใบนี้ใช้จริง" ให้ครบเหมือนไฟล์จริง (เช่น é ใน
# želé) ไม่งั้นชุดอักขระที่เปื้อนจะกว้างเกินจริงและเทสต์จะวัดผิดเรื่อง
TRUSTED_TEXT = ("SLOŽENÍ: pacifický tuňák v želé (47,8 %), rýže (1,0 %), "
                "chlorid draselný, vývar (50,2 %). DOPLŇKOVÉ KRMIVO")


def spans(*items):
    return [{"font": f, "text": t} for f, t in items]


REAL_DOC = spans(
    (BROKEN_FONT, HARD_EVIDENCE),
    (BROKEN_FONT, SILENT_GARBAGE),
    (BROKEN_FONT, GOOD_SLOVAK),
    (BROKEN_FONT, GOOD_ENGLISH),
    (CLEAN_FONT, TRUSTED_TEXT),
)


# ── A. หาว่าฟอนต์ไหนพัง ──────────────────────────────────────────────

def test_font_with_hard_evidence_is_suspect():
    t = fonttrust.analyze(REAL_DOC)
    assert t["suspect"] == [BROKEN_FONT]


def test_healthy_document_has_no_suspect_and_is_never_touched():
    """คุณสมบัติที่ทำให้กล้าเปิดเป็นค่าเริ่มต้น: ไฟล์ปกติต้องไม่ถูกแตะเลย."""
    t = fonttrust.analyze(spans((CLEAN_FONT, TRUSTED_TEXT),
                                (BROKEN_FONT, GOOD_SLOVAK)))
    assert t["suspect"] == [] and t["poisoned"] == ""
    for f, txt in ((BROKEN_FONT, GOOD_SLOVAK), (CLEAN_FONT, TRUSTED_TEXT)):
        assert fonttrust.span_reason(f, txt, t) == ""


def test_evidence_counts_are_reported_per_font():
    t = fonttrust.analyze(REAL_DOC)
    ev = fonttrust.summary(t)["evidence"][BROKEN_FONT]
    assert ev["spans"] == 4 and ev["bad_spans"] == 1 and ev["bad_chars"] == 3


# ── B. หัวใจ: จับขยะที่ "ไม่มีอักขระต้องห้าม" ────────────────────────

def test_silent_garbage_of_a_broken_font_is_rejected():
    """เคสที่ด่านรายโซนจับไม่ได้ — ต้องถูกจับด้วยหลักฐานระดับฟอนต์."""
    t = fonttrust.analyze(REAL_DOC)
    assert ocr.text_looks_garbled(SILENT_GARBAGE) is False, (
        "ยืนยันสมมติฐาน: ด่านรายโซนมองไม่เห็นข้อความก้อนนี้")
    assert fonttrust.span_reason(BROKEN_FONT, SILENT_GARBAGE, t)


def test_correct_text_of_the_same_broken_font_is_kept():
    """ไม่เหวี่ยงแห — ข้อความที่ถูกต้องของฟอนต์เดียวกันต้องยังใช้ได้
    (โหมด chars). ไม่งั้นจะเสียความแม่นของ text layer ไปฟรี ๆ."""
    t = fonttrust.analyze(REAL_DOC)
    assert fonttrust.span_reason(BROKEN_FONT, GOOD_ENGLISH, t) == ""
    assert fonttrust.span_reason(BROKEN_FONT, GOOD_SLOVAK, t) == ""


def test_trusted_font_is_never_touched_even_in_a_broken_document():
    t = fonttrust.analyze(REAL_DOC)
    assert fonttrust.span_reason(CLEAN_FONT, TRUSTED_TEXT, t) == ""
    # แม้ข้อความของฟอนต์ที่เชื่อได้จะมีอักขระหน้าตาเดียวกับที่ "เปื้อน"
    assert fonttrust.span_reason(CLEAN_FONT, "Ï/=", t) == ""


def test_poisoned_set_must_come_from_trusted_fonts_not_from_itself():
    """⚠️ กับดักที่เจอตอนวัดจริง: ถ้าคำนวณชุด "อักขระที่เปื้อน" โดยเทียบกับ
    span ที่ "สะอาด" ของฟอนต์เดียวกัน ตัวขยะที่ตรวจไม่เจอจะ **ลบตัวเองออก
    จากรายการ** (span 'Ï/=' ทำให้ Ï กลายเป็นอักขระปกติของฟอนต์นั้น) ⇒
    ชั้นนี้จะไม่จับอะไรเลย. ฐานเทียบต้องเป็น **ฟอนต์อื่นที่ไม่มีร่องรอย**
    """
    t = fonttrust.analyze(REAL_DOC)
    assert "Ï" in t["poisoned"], (
        "Ï ต้องอยู่ในชุดที่เปื้อน — ถ้าหลุดแปลว่าฐานเทียบถูกเปลี่ยนไปใช้ "
        "span ของฟอนต์ที่พังเอง ซึ่งทำให้ชั้นนี้ไร้ผล")
    # อักขระที่ฟอนต์ที่เชื่อได้ใช้จริง ต้องไม่ถูกเหมาว่าเปื้อน
    for ch in "ŽÍňáéý":
        assert ch not in t["poisoned"], ch


# ── C. โหมดความเข้ม ──────────────────────────────────────────────────

@pytest.mark.parametrize("mode,expect_slovak,expect_english", [
    ("chars", False, False),      # ค่าเริ่มต้น — แม่นที่สุดที่วัดได้
    ("nonascii", True, False),    # เข้มขึ้น: อะไรที่ไม่ใช่ ASCII โดนหมด
    ("font", True, True),         # เข้มสุด: ทุก span ของฟอนต์ที่พัง
])
def test_modes_scale_from_surgical_to_blanket(mode, expect_slovak,
                                              expect_english):
    t = fonttrust.analyze(REAL_DOC, mode=mode)
    assert bool(fonttrust.span_reason(BROKEN_FONT, GOOD_SLOVAK, t)) is expect_slovak
    assert bool(fonttrust.span_reason(BROKEN_FONT, GOOD_ENGLISH, t)) is expect_english
    # ทุกโหมดต้องจับขยะเงียบได้เสมอ
    assert fonttrust.span_reason(BROKEN_FONT, SILENT_GARBAGE, t)


def test_off_mode_is_the_old_behaviour_exactly():
    t = fonttrust.analyze(REAL_DOC, mode="off")
    assert t["mode"] == "off"
    for txt in (SILENT_GARBAGE, HARD_EVIDENCE, GOOD_SLOVAK):
        assert fonttrust.span_reason(BROKEN_FONT, txt, t) == ""


def test_unknown_mode_falls_back_to_off_not_to_guessing():
    t = fonttrust.analyze(REAL_DOC, mode="ตั้งค่าผิด")
    assert t["mode"] == "off"
    assert fonttrust.span_reason(BROKEN_FONT, SILENT_GARBAGE, t) == ""


# ── D. ระดับโซน ──────────────────────────────────────────────────────

def test_zone_with_one_bad_span_is_rejected_whole():
    """โซนคือหน่วยที่ชั้นตรวจเอาไปเทียบ — มีเศษที่แมปผิดปนอยู่ก็เชื่อไม่ได้
    ทั้งก้อน (การตัดเฉพาะบางคำทิ้งจะทำให้ข้อความที่เหลือ "ดูครบ" ทั้งที่หาย)"""
    t = fonttrust.analyze(REAL_DOC)
    zone = spans((BROKEN_FONT, GOOD_SLOVAK), (BROKEN_FONT, SILENT_GARBAGE))
    assert fonttrust.zone_reason(zone, t)


def test_zone_of_only_good_spans_is_kept():
    t = fonttrust.analyze(REAL_DOC)
    zone = spans((BROKEN_FONT, GOOD_ENGLISH), (CLEAN_FONT, TRUSTED_TEXT))
    assert fonttrust.zone_reason(zone, t) == ""


def test_reason_names_the_font_so_the_user_can_act_on_it():
    """ต้นเหตุจริงอยู่ที่ขั้นตอน export ของคนทำ artwork ⇒ รายงานต้องบอกชื่อ
    ฟอนต์ ไม่ใช่แค่ "อ่านไม่ได้"."""
    t = fonttrust.analyze(REAL_DOC)
    assert BROKEN_FONT in fonttrust.span_reason(BROKEN_FONT, SILENT_GARBAGE, t)


# ── E. กันฟ้องผิดกับภาษาที่ใช้อักขระควบคุมเป็นปกติ ───────────────────

def test_bidi_marks_never_make_a_font_suspect():
    """ZWJ/RLM (category Cf) เป็นของปกติบนฉลากอาหรับ/ฮีบรู — ถ้านับเป็น
    หลักฐาน ฟอนต์อาหรับทุกตัวจะกลายเป็น "พัง" แล้วทั้งใบตกไป OCR."""
    ar = "‏مكونات: تونة المحيط الهادئ ٤٧٫٨٪ ‎ أرز ‍ ١٫٠٪ مرق"
    he = "‎מרכיבים: טונה אוקיינוס שקט 47.8% אורז 1.0%"
    t = fonttrust.analyze(spans(("ArabicFont", ar), ("HebrewFont", he)))
    assert t["suspect"] == []


def test_summary_is_json_safe():
    import json
    json.dumps(fonttrust.summary(fonttrust.analyze(REAL_DOC)),
               ensure_ascii=False)
    assert fonttrust.summary({})["suspect"] == []


# ── F. ต่อเข้าเส้นทางจริงของ read_zone ───────────────────────────────

class FakeDoc(object):
    """เอกสารปลอมที่คืน text layer + span ตามที่กำหนด."""

    is_pdf = True

    def __init__(self, text, zone_spans):
        self.text = text
        self._spans = zone_spans
        self.render_calls = 0

    def embedded_text(self, bbox=None):
        return self.text

    def text_spans(self, bbox_norm=None, all_pages=False, max_pages=12):
        return self._spans

    def render_zone(self, bbox, dpi, max_side=None):
        self.render_calls += 1
        return np.full((900, 1400, 3), 255, np.uint8)


ZONE = {"id": "z1", "type": "panel", "bbox": [0.1, 0.1, 0.2, 0.3], "rotate": 0}
# ข้อความของโซนที่ "ดูสะอาด" แต่มาจากฟอนต์ที่พิสูจน์แล้วว่าพัง
DIRTY_ZONE_TEXT = GOOD_SLOVAK + " " + SILENT_GARBAGE


@pytest.fixture
def ocr_on(monkeypatch):
    monkeypatch.setattr(ocr.vertex_client, "is_enabled", lambda: True)
    monkeypatch.setattr(ocr.vertex_client, "ocr_image",
                        lambda b: {"text": "FROM OCR", "blocks": [],
                                   "engine": "mock"})


def test_read_zone_falls_back_to_ocr_on_font_evidence(ocr_on):
    trust = fonttrust.analyze(REAL_DOC)
    doc = FakeDoc(DIRTY_ZONE_TEXT,
                  spans((BROKEN_FONT, GOOD_SLOVAK),
                        (BROKEN_FONT, SILENT_GARBAGE)))
    assert ocr.text_looks_garbled(DIRTY_ZONE_TEXT) is False, "ด่านเดิมมองไม่เห็น"
    r = ocr.read_zone(doc, ZONE, font_trust=trust)
    assert r["engine"] == "mock" and r["text"] == "FROM OCR"
    assert BROKEN_FONT in r.get("note", "")


def test_read_zone_without_font_trust_is_the_old_path(ocr_on):
    """rollback: ไม่ส่ง font_trust = พฤติกรรมเดิมเป๊ะ."""
    doc = FakeDoc(DIRTY_ZONE_TEXT, [])
    r = ocr.read_zone(doc, ZONE)
    assert r["engine"] == "pdf-text" and r["conf"] == 1.0
    assert doc.render_calls == 0


def test_healthy_document_costs_nothing(ocr_on, monkeypatch):
    """ไม่มีฟอนต์ต้องสงสัย ⇒ ต้องไม่อ่าน span เพิ่มแม้แต่ครั้งเดียว."""
    trust = fonttrust.analyze(spans((CLEAN_FONT, TRUSTED_TEXT)))
    doc = FakeDoc(TRUSTED_TEXT, [])
    called = []
    monkeypatch.setattr(doc, "text_spans",
                        lambda *a, **k: called.append(1) or [])
    r = ocr.read_zone(doc, ZONE, font_trust=trust)
    assert r["engine"] == "pdf-text" and called == []


def test_span_read_failure_never_breaks_the_zone(ocr_on):
    """ชั้นนี้เป็นตัวช่วย — อ่าน span ไม่ได้ต้องไม่ทำให้โซนพัง."""
    trust = fonttrust.analyze(REAL_DOC)

    class Boom(FakeDoc):
        def text_spans(self, *a, **k):
            raise RuntimeError("อ่าน span ไม่ได้")

    r = ocr.read_zone(Boom(TRUSTED_TEXT, []), ZONE, font_trust=trust)
    assert r["engine"] == "pdf-text" and r["text"] == TRUSTED_TEXT


def test_font_trust_is_off_for_non_pdf():
    class Img(object):
        is_pdf = False
    assert pipeline.font_trust(Img())["mode"] == "off"


def test_font_trust_respects_the_config_switch(monkeypatch):
    monkeypatch.setattr(config, "PDFTEXT_FONT_EVIDENCE", "off")

    class Doc(object):
        is_pdf = True
        def text_spans(self, *a, **k):
            raise AssertionError("ปิดอยู่ ต้องไม่อ่าน span เลย")

    assert pipeline.font_trust(Doc())["mode"] == "off"
