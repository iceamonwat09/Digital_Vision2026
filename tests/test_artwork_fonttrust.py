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

# ── G. หลักฐานทางที่ 2: โครงสร้างไฟล์ (ไม่ต้องรออาการ) ────────────────
#
# ทำไมต้องมีทั้งสองทาง — วัดบนไฟล์จริง 833 บรรทัด (เสียจริง 70):
#     ด่านอักขระอย่างเดียว          จับ 58 · ฟ้องผิด  0
#     + หลักฐานจากอาการ (chars)     จับ 70 · ฟ้องผิด 10
#     + หลักฐานจากโครงสร้าง         จับ 70 · ฟ้องผิด 10   ← ไม่เพิ่มการฟ้องผิด
# ทางที่ 2 ปิดช่องโหว่เชิงหลักการ: ถ้าฟอนต์พังแล้วบังเอิญคายแต่ตัวอักษรที่
# ดูปกติ ทางที่ 1 จะตาบอดสนิท ส่วนทางที่ 2 ยังจับได้เพราะโครงสร้างไฟล์
# ไม่เปลี่ยนไปตามอาการ

def test_structural_evidence_alone_makes_a_font_suspect():
    """หัวใจของทางที่ 2: ฟอนต์ที่ **ไม่คายอักขระเสียเลย** แต่ฝังมาแบบถอดกลับ
    ไม่ได้ ต้องถูกจับได้ — ทางที่ 1 (อาการ) ตาบอดกับเคสนี้."""
    doc = spans((BROKEN_FONT, GOOD_SLOVAK),         # ไม่มีอักขระต้องห้ามเลย
                (BROKEN_FONT, SILENT_GARBAGE),
                (CLEAN_FONT, TRUSTED_TEXT))
    by_symptom = fonttrust.analyze(doc)
    assert by_symptom["suspect"] == [], "ยืนยันว่าทางที่ 1 ตาบอดกับชุดนี้"

    t = fonttrust.analyze(doc, unmappable_fonts=[BROKEN_FONT])
    assert t["suspect"] == [BROKEN_FONT]
    assert fonttrust.span_reason(BROKEN_FONT, SILENT_GARBAGE, t)


def test_why_records_which_evidence_fired():
    """ผู้ใช้ต้องรู้ว่ารู้ได้อย่างไร — สองทางนี้แก้คนละแบบ."""
    only_struct = fonttrust.analyze(
        spans((BROKEN_FONT, GOOD_SLOVAK), (CLEAN_FONT, TRUSTED_TEXT)),
        unmappable_fonts=[BROKEN_FONT])
    assert only_struct["why"][BROKEN_FONT] == ["structure"]

    both = fonttrust.analyze(REAL_DOC, unmappable_fonts=[BROKEN_FONT])
    assert both["why"][BROKEN_FONT] == ["chars", "structure"]

    only_chars = fonttrust.analyze(REAL_DOC)
    assert only_chars["why"][BROKEN_FONT] == ["chars"]


def test_structural_reason_explains_the_root_cause():
    # ⚠️ ต้องมี span ที่มีอักขระ "เปื้อน" อยู่ในเอกสารด้วย ไม่งั้นโหมด chars
    # ไม่มีอะไรให้จำกัดขอบเขต (หลักฐานเชิงโครงสร้างบอกว่า "ฟอนต์ไหน" —
    # ส่วนโหมดยังเป็นตัวบอกว่า "แตะ span ไหนของฟอนต์นั้น")
    t = fonttrust.analyze(spans((BROKEN_FONT, GOOD_SLOVAK),
                                (BROKEN_FONT, SILENT_GARBAGE),
                                (CLEAN_FONT, TRUSTED_TEXT)),
                          unmappable_fonts=[BROKEN_FONT])
    msg = fonttrust.span_reason(BROKEN_FONT, SILENT_GARBAGE, t) or ""
    assert "ToUnicode" in msg, "ต้องบอกต้นเหตุ ไม่ใช่แค่ 'เชื่อไม่ได้'"


def test_structure_says_which_font_mode_says_which_span():
    """แบ่งหน้าที่ให้ชัด: หลักฐานเชิงโครงสร้าง = "ฟอนต์ไหนน่าสงสัย" ·
    โหมด (chars/nonascii/font) = "แตะ span ไหนของฟอนต์นั้น".
    ⇒ ในโหมด chars ถ้าฟอนต์ที่เชื่อไม่ได้บังเอิญคายแต่อักขระที่งานนี้ใช้จริง
    ก็จะยังไม่ถูกแตะ — ถ้าต้องการเข้มกว่านั้นให้ตั้งโหมดเป็น font"""
    doc = spans((BROKEN_FONT, GOOD_ENGLISH), (CLEAN_FONT, TRUSTED_TEXT))
    t_chars = fonttrust.analyze(doc, unmappable_fonts=[BROKEN_FONT])
    assert t_chars["suspect"] == [BROKEN_FONT]
    assert fonttrust.span_reason(BROKEN_FONT, GOOD_ENGLISH, t_chars) == ""

    t_font = fonttrust.analyze(doc, mode="font",
                               unmappable_fonts=[BROKEN_FONT])
    assert fonttrust.span_reason(BROKEN_FONT, GOOD_ENGLISH, t_font)


def test_structural_suspicion_still_respects_the_chars_scope():
    """ทางที่ 2 บอกแค่ว่า "ฟอนต์ไหนน่าสงสัย" — ขอบเขตยังคุมด้วยโหมดเดิม
    (ไม่เหวี่ยงแหทั้งฟอนต์เมื่ออยู่โหมด chars)."""
    t = fonttrust.analyze(REAL_DOC, unmappable_fonts=[BROKEN_FONT])
    assert fonttrust.span_reason(BROKEN_FONT, GOOD_ENGLISH, t) == ""
    assert fonttrust.span_reason(BROKEN_FONT, SILENT_GARBAGE, t)


def test_structural_list_of_a_healthy_document_changes_nothing():
    t = fonttrust.analyze(spans((CLEAN_FONT, TRUSTED_TEXT)),
                          unmappable_fonts=[])
    assert t["suspect"] == [] and t["why"] == {}


# ── H. เกณฑ์เชิงโครงสร้างบน PDF จริง (สร้างไฟล์จิ๋วในเทสต์) ──────────

def _mini_pdf(path, kind):
    """PDF จิ๋วที่มีฟอนต์แบบเดียวตามที่ระบุ — เขียนไบต์เอง เพื่อให้เทสต์
    ไม่ต้องพึ่งไฟล์ของลูกค้า และควบคุมโครงสร้างได้เป๊ะ.

    kind: "cid"      = Type0/Identity-H **ไม่มี** ToUnicode  (เชื่อไม่ได้)
          "cid_tu"   = Type0/Identity-H **มี** ToUnicode      (เชื่อได้)
          "simple"   = Type1/WinAnsiEncoding ไม่มี ToUnicode  (เชื่อได้)
    """
    tu = "/ToUnicode 8 0 R" if kind == "cid_tu" else ""
    if kind == "simple":
        font = ("<< /Type /Font /Subtype /Type1 /BaseFont /ABCDEF+TestSimple "
                "/Encoding /WinAnsiEncoding >>")
        desc = "<< /Type /XObject >>"
    else:
        font = ("<< /Type /Font /Subtype /Type0 /BaseFont /ABCDEF+TestCID "
                "/Encoding /Identity-H /DescendantFonts [5 0 R] %s >>" % tu)
        desc = ("<< /Type /Font /Subtype /CIDFontType2 "
                "/BaseFont /ABCDEF+TestCID /CIDSystemInfo << /Registry (Adobe) "
                "/Ordering (Identity) /Supplement 0 >> /FontDescriptor 6 0 R "
                "/DW 500 >>")
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Resources "
        "<< /Font << /F1 4 0 R >> >> /Contents 7 0 R >>",
        font, desc,
        "<< /Type /FontDescriptor /FontName /ABCDEF+TestCID /Flags 4 "
        "/FontBBox [0 0 500 700] /ItalicAngle 0 /Ascent 700 /Descent -200 "
        "/CapHeight 700 /StemV 80 >>",
        None, None,
    ]
    stream = b"BT /F1 12 Tf 10 40 Td <00240025> Tj ET"
    cmap = (b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
            b"1 begincodespacerange <0000> <FFFF> endcodespacerange\n"
            b"1 beginbfrange <0024> <0025> <0041> endbfrange\n"
            b"endcmap CMapName currentdict /CMap defineresource pop end end")
    out = bytearray(b"%PDF-1.6\n")
    offs = {}
    for i, body in enumerate(objs, 1):
        offs[i] = len(out)
        if i == 7:
            out += (b"7 0 obj\n<< /Length %d >>\nstream\n" % len(stream)
                    + stream + b"\nendstream\nendobj\n")
        elif i == 8:
            if kind != "cid_tu":
                continue
            out += (b"8 0 obj\n<< /Length %d >>\nstream\n" % len(cmap)
                    + cmap + b"\nendstream\nendobj\n")
        else:
            out += ("%d 0 obj\n%s\nendobj\n" % (i, body)).encode()
    xref_at = len(out)
    out += b"xref\n0 9\n0000000000 65535 f \n"
    for i in range(1, 9):
        out += b"%010d 00000 n \n" % offs.get(i, 0)
    out += (b"trailer\n<< /Size 9 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % xref_at)
    with open(path, "wb") as f:
        f.write(bytes(out))
    return path


@pytest.mark.parametrize("kind,expect", [
    ("cid", ["TestCID"]),      # Identity-H ไม่มี ToUnicode = ถอดกลับไม่ได้
    ("cid_tu", []),            # มี ToUnicode = ถอดกลับได้ ⇒ ห้ามสงสัย
    ("simple", []),            # WinAnsiEncoding = encoding มาตรฐาน ⇒ ห้ามสงสัย
])
def test_unmappable_font_criterion_on_real_pdfs(tmp_path, kind, expect):
    """⚠️ เกณฑ์ต้อง **แคบ** — ไฟล์ที่ฟอนต์ปกติมักไม่มี ToUnicode เลยสักตัว
    (วัดจากไฟล์จริง: ทั้งสองไฟล์ 0/14 ฟอนต์มี ToUnicode) ⇒ ถ้าใช้แค่
    "ไม่มี ToUnicode" เป็นเกณฑ์จะฟ้องผิดทั้งโลก. ตัวชี้ขาดคือ **Identity-H**
    ซึ่งแปลว่า "เลขคือหมายเลข glyph" จึงไม่มีทางถอดกลับได้ถ้าไม่มีตาราง."""
    from artwork_check.pdf_ingest import ArtworkDocument
    p = _mini_pdf(str(tmp_path / ("%s.pdf" % kind)), kind)
    assert ArtworkDocument(p).unmappable_fonts() == expect


def test_unmappable_fonts_strips_the_subset_prefix(tmp_path):
    """ชื่อในตารางฟอนต์มี prefix (`ABCDEF+`) แต่ชื่อใน span ไม่มี — ถ้าไม่ตัด
    ให้ตรงกัน ชั้นนี้จะไม่เคยจับคู่กับข้อความได้เลย (พังแบบเงียบ)."""
    from artwork_check.pdf_ingest import ArtworkDocument
    p = _mini_pdf(str(tmp_path / "cid.pdf"), "cid")
    assert ArtworkDocument(p).unmappable_fonts() == ["TestCID"]


def test_pipeline_feeds_structural_evidence_into_the_analysis():
    """เส้นทางจริง: ``font_trust`` ต้องส่งรายชื่อฟอนต์ที่ถอดกลับไม่ได้เข้าไป
    ใน ``analyze`` — ถ้าลืมส่ง ชั้นนี้จะเงียบสนิทโดยไม่มี error."""
    class Doc(object):
        is_pdf = True
        def text_spans(self, *a, **k):
            # ไม่มีอักขระต้องห้ามเลย ⇒ ถ้าไม่มีหลักฐานเชิงโครงสร้างจะไม่สงสัย
            return [{"font": "BadCID", "text": "Ï/="},
                    {"font": "OkFont", "text": "ส่วนผสม: ปลาทูน่า"}]
        def unmappable_fonts(self, *a, **k):
            return ["BadCID"]

    t = pipeline.font_trust(Doc())
    assert t["suspect"] == ["BadCID"]
    assert t["why"]["BadCID"] == ["structure"]
    assert fonttrust.span_reason("BadCID", "Ï/=", t)


def test_structure_check_can_be_switched_off(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PDFTEXT_FONT_STRUCTURE_CHECK", False)
    called = []

    class Doc(object):
        is_pdf = True
        def text_spans(self, *a, **k):
            return [{"font": "F", "text": "ok"}]
        def unmappable_fonts(self, *a, **k):
            called.append(1)
            return ["F"]

    t = pipeline.font_trust(Doc())
    assert called == [] and t["suspect"] == []
