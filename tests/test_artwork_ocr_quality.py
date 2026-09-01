"""
ทดสอบสองด่านคุณภาพของชั้นอ่านข้อความ (artwork_check/ocr.py):

  A. โซนที่เรนเดอร์ออกมาเล็กเกินไป ต้องถูกเรนเดอร์ใหม่ที่ DPI สูงขึ้น
     ก่อนส่ง OCR — เดิมมีเฉพาะภาพบนการ์ดที่คนดู ไม่มีในเส้นทางที่ OCR ได้รับ
     (วัดจากไฟล์จริง: บล็อก 253 คำได้ 231x337 px -> recall 1.2%;
      เรนเดอร์ใหม่ให้ด้านยาว 1200 px -> 97.6%)

  B. text layer ที่ "มีข้อความพอ" แต่ฟอนต์แมปอักขระผิด ต้องไม่ถูกส่งต่อ
     ด้วย conf 1.0 — ต้องตกไปใช้ OCR แทน

ทั้งคู่ต้องปิดได้แล้วกลับเป็นพฤติกรรมเดิมเป๊ะ.
"""

import numpy as np
import pytest

from artwork_check import config, ocr


# ── ตัวช่วย: เอกสารปลอมที่บันทึกว่าถูกขอเรนเดอร์ที่ DPI ไหนบ้าง ──────────

class FakeDoc(object):
    def __init__(self, embedded="", first=(200, 300), is_pdf=True):
        self.embedded = embedded
        self.first = first          # (w, h) ของการเรนเดอร์ครั้งแรก
        self.is_pdf = is_pdf
        self.calls = []             # dpi ที่ถูกเรียกตามลำดับ

    def embedded_text(self, bbox=None):
        return self.embedded

    def render_zone(self, bbox, dpi, max_side=None):
        self.calls.append(dpi)
        scale = dpi / float(config.OCR_DPI)
        w = max(1, int(self.first[0] * scale))
        h = max(1, int(self.first[1] * scale))
        if max_side and max(w, h) > max_side:
            s = max_side / float(max(w, h))
            w, h = max(1, int(w * s)), max(1, int(h * s))
        return np.full((h, w, 3), 255, np.uint8)


ZONE = {"id": "z1", "type": "panel", "bbox": [0.1, 0.1, 0.2, 0.3],
        "rotate": 0}


@pytest.fixture
def ocr_on(monkeypatch):
    """ทำให้ backend OCR "พร้อมใช้" และคืนข้อความคงที่."""
    monkeypatch.setattr(ocr.vertex_client, "is_enabled", lambda: True)
    monkeypatch.setattr(ocr.vertex_client, "ocr_image",
                        lambda b: {"text": "FROM OCR", "blocks": [],
                                   "engine": "mock"})


# ── A. เพิ่ม DPI ให้โซนเล็ก ──────────────────────────────────────────

def test_small_zone_is_rerendered_at_higher_dpi(ocr_on, monkeypatch):
    monkeypatch.setattr(config, "OCR_CROP_MIN_SIDE", 1200)
    doc = FakeDoc(first=(231, 337))            # ขนาดจริงจากไฟล์ A4 ที่พบ
    r = ocr.read_zone(doc, ZONE)
    assert len(doc.calls) == 2, "ต้องเรนเดอร์ซ้ำเมื่อภาพเล็กเกินไป"
    assert doc.calls[1] > doc.calls[0]
    assert r["engine"] == "mock"


def test_large_zone_is_not_rerendered(ocr_on, monkeypatch):
    monkeypatch.setattr(config, "OCR_CROP_MIN_SIDE", 1200)
    doc = FakeDoc(first=(788, 1400))           # ด้านยาวเกิน 1200 อยู่แล้ว
    ocr.read_zone(doc, ZONE)
    assert doc.calls == [config.OCR_DPI], "ภาพใหญ่พอแล้วต้องไม่เรนเดอร์ซ้ำ"


def test_boost_disabled_restores_old_behaviour(ocr_on, monkeypatch):
    monkeypatch.setattr(config, "OCR_CROP_MIN_SIDE", 0)
    doc = FakeDoc(first=(231, 337))
    ocr.read_zone(doc, ZONE)
    assert doc.calls == [config.OCR_DPI]


def test_raster_image_is_never_upscaled(ocr_on, monkeypatch):
    """ภาพถ่ายไม่มีรายละเอียดเพิ่มให้ดึง — ขยายได้แค่ความเบลอ."""
    monkeypatch.setattr(config, "OCR_CROP_MIN_SIDE", 1200)
    doc = FakeDoc(first=(231, 337), is_pdf=False)
    ocr.read_zone(doc, ZONE)
    assert doc.calls == [config.OCR_DPI]


def test_boost_respects_max_factor(ocr_on, monkeypatch):
    monkeypatch.setattr(config, "OCR_CROP_MIN_SIDE", 1200)
    monkeypatch.setattr(config, "OCR_DPI_MAX_FACTOR", 2.0)
    doc = FakeDoc(first=(30, 40))              # ต้องใช้ x30 ถ้าไม่มีเพดาน
    ocr.read_zone(doc, ZONE)
    assert doc.calls[1] <= config.OCR_DPI * 2.0 + 1


# ── B. ตัวตรวจ text layer เสีย ───────────────────────────────────────

GOOD_TEXTS = [
    # เยอรมัน/เช็ก/โปแลนด์ ของจริง
    "ERGANZUNGSFUTTERMITTEL FUR AUSGEWACHSENE KATZEN ZUSAMMENSETZUNG "
    "PAZIFIKTHUNFISCH IN GELEE ANALYTISCHE BESTANDTEILE ROHPROTEIN "
    "ROHFETTGEHALT ROHASCHE FEUCHTIGKEIT KRMIVO PRO DOSPELE KOCKY",
    # ฉลากที่มีรหัส/สารเติมแต่ง/น้ำหนักเยอะ
    "INGREDIENTS: TUNA 60%, WATER, SALT, VITAMIN B12, OMEGA-3, E1520 "
    "PROPYLENE GLYCOL, NET WT 170G, LOT A5512, EAN 8850001111228, "
    "DISTRIBUTED BY OCEAN FOODS COMPANY LIMITED BANGKOK THAILAND",
    # แถบรหัสงานพิมพ์ (คำน้อย — ต้องไม่ตัดสิน)
    "5K00D111N000000301 TUG-15974R04-0-M C6903009R1 AWN202600005644",
]

GARBLED_TEXTS = [
    "1MUWSXYCGOEFSRMXSKEPEVIXGISKLAD ASWSGKEPEVIXGISKLAD "
    "4MIVGOYVGEOEKEPEVIXGISKLAD 8YCGOSASMREKEPEVIXGISKLAD "
    "8YCGOQMUWIQVEOKEPEVIXGI PEGMJMGOAXYCOZLILE ROL12SSAL 0SWSWZLILE",
    "340CKOVE PR3374Y0KOI TYCKOVE 0SWSWZLILE PRWEZLILE ROL12SSAL "
    "PEGMJMGOAXYCOZLILE 8YCGOSASMRE 1MUWSXYCGOEF 4MIVGOYVGEOE",
]


@pytest.mark.parametrize("text", GOOD_TEXTS)
def test_real_text_is_not_flagged(text):
    assert ocr.text_looks_garbled(text) is False


@pytest.mark.parametrize("text", GARBLED_TEXTS)
def test_garbled_text_is_flagged(text):
    assert ocr.text_looks_garbled(text) is True


def test_garbled_text_layer_falls_back_to_ocr(ocr_on, monkeypatch):
    monkeypatch.setattr(config, "PDFTEXT_GARBLED_CHECK", True)
    doc = FakeDoc(embedded=GARBLED_TEXTS[0], first=(1400, 900))
    r = ocr.read_zone(doc, ZONE)
    assert r["engine"] == "mock", "ต้องไม่ใช้ค่าจาก text layer ที่เสีย"
    assert r["text"] == "FROM OCR"
    assert r.get("conf") != 1.0
    assert "note" in r, "ต้องบอกเหตุผลไว้ในผล"


def test_clean_text_layer_still_short_circuits(ocr_on, monkeypatch):
    monkeypatch.setattr(config, "PDFTEXT_GARBLED_CHECK", True)
    doc = FakeDoc(embedded=GOOD_TEXTS[0], first=(1400, 900))
    r = ocr.read_zone(doc, ZONE)
    assert r["engine"] == "pdf-text"
    assert r["conf"] == 1.0
    assert doc.calls == [], "ไม่ควรเรนเดอร์ภาพเลยเมื่อ text layer ใช้ได้"


def test_garbled_check_disabled_restores_old_behaviour(ocr_on, monkeypatch):
    monkeypatch.setattr(config, "PDFTEXT_GARBLED_CHECK", False)
    doc = FakeDoc(embedded=GARBLED_TEXTS[0], first=(1400, 900))
    r = ocr.read_zone(doc, ZONE)
    assert r["engine"] == "pdf-text"
    assert r["conf"] == 1.0


def test_garbled_without_ocr_backend_is_flagged_unreadable(monkeypatch):
    """ไม่มี OCR ให้ถอยไปใช้ — ต้องติดธง error เพื่อให้คนมาดู
    ไม่ใช่ส่งข้อความมั่วต่อไปเงียบ ๆ."""
    monkeypatch.setattr(config, "PDFTEXT_GARBLED_CHECK", True)
    monkeypatch.setattr(ocr.vertex_client, "is_enabled", lambda: False)
    doc = FakeDoc(embedded=GARBLED_TEXTS[1], first=(1400, 900))
    r = ocr.read_zone(doc, ZONE)
    assert r.get("error"), "ต้องมี error เพื่อให้กลายเป็น UNREADABLE"
    assert r.get("conf") != 1.0


def test_short_text_is_never_judged():
    """ข้อความสั้นกว่าเกณฑ์ต้องไม่ถูกตัดสินว่าเสีย (กันรหัสงานพิมพ์)."""
    assert ocr.text_looks_garbled("PR3374Y0KOI ROL12SSAL") is False


# ── B2. ด่านอักขระต้องห้าม (control / PUA / U+FFFD) ──────────────────
#
# ที่มา: ไฟล์จริง 2 ฉบับของงานเดียวกัน (Cosma Schlemmerbox) — เนื้อหาเดียวกัน
# เป๊ะ ฟอนต์ชื่อเดียวกัน (DINPro-Bold) แต่ subset คนละชุด:
#   Original : "DOPLŇKOVÉ KRMIVO PRO DOSPĚLÉ KOČKY"   ← text layer ถูกต้อง
#   ฉบับ A4  : "(340ĊKOVÉ KRMIVO PR3\x04(374Ý0\x8c…"  ← ToUnicode CMap พัง
# ด่าน ratio เดิม **ไม่ฟ้อง** เคสนี้ (คำผิดรูป 2/14 = 14.3% < 30%) และหนึ่งใน
# คำที่พังไม่มีตัวเลขเลยจึงไม่มีทางถูกจับด้วยกฎ "ตัวเลขกลางคำ" ไม่ว่าจะตั้ง
# ratio ต่ำแค่ไหน ⇒ ต้องมีด่านที่ดูชนิดของอักขระแทน

# ข้อความจริงที่ดึงจากไฟล์ A4 ด้วย PyMuPDF (ตัดมา 450 ตัวอักษรแรกของโซน)
REAL_BROKEN_ZONE = (
    "odzin. Zapewnić dostęp do świeżej wody.\n"
    "(340ĊKOVÉ KRMIVO PR3\x04(374Ý0\x8c\x04KOÏ/=\n"
    "PEGMJMGOÀ\x04XYċ¤O\x04Z\x04ŁIlé. SLOŽENÍ: pacifický tuňák (47,8 %), "
    "rýže (1,0 %), chlorid \n"
    "draselný, vývar (50,2 %). ANALYTICKÉ SLOŽKY: hrubý protein 13,0 %, "
    "hrubý \ntuk 0,5 %, hrubý popel 1,5 %, hrubá vláknina 0,1 %, "
    "vlhkost 84,0 %.\n"
)
# ข้อความจริงจากไฟล์ Original (เนื้อหาเดียวกัน แต่ฟอนต์แมปถูก)
REAL_CLEAN_ZONE = (
    "odzin. Zapewnić dostęp do świeżej wody.\n"
    "DOPLŇKOVÉ KRMIVO PRO DOSPĚLÉ KOČKY\n"
    "Pacifický tuňák v želé. SLOŽENÍ: pacifický tuňák (47,8 %), "
    "rýže (1,0 %), chlorid \n"
    "draselný, vývar (50,2 %). ANALYTICKÉ SLOŽKY: hrubý protein 13,0 %, "
    "hrubý \ntuk 0,5 %, hrubý popel 1,5 %, hrubá vláknina 0,1 %, "
    "vlhkost 84,0 %.\n"
)


def test_real_broken_file_zone_is_flagged():
    """เคสที่จุดชนวนงานนี้ — ต้องจับได้."""
    assert ocr.text_looks_garbled(REAL_BROKEN_ZONE) is True


def test_real_clean_file_zone_is_not_flagged():
    """ไฟล์ที่ฟอนต์ดี (เนื้อหาเดียวกันเป๊ะ) ต้องไม่ถูกฟ้อง."""
    assert ocr.text_looks_garbled(REAL_CLEAN_ZONE) is False


def test_the_gap_this_layer_closes(monkeypatch):
    """พิสูจน์ว่าด่านเดิมจับเคสนี้ไม่ได้จริง ๆ — ไม่ใช่แค่เพิ่มโค้ดซ้ำซ้อน.

    ถ้าวันหนึ่งมีคนลบด่านอักขระทิ้งแล้วบอกว่า "ด่าน ratio ก็พอ" เทสต์นี้จะแดง
    """
    monkeypatch.setattr(config, "PDFTEXT_BAD_GLYPH_CHECK", False)
    assert ocr.text_looks_garbled(REAL_BROKEN_ZONE) is False, (
        "ด่าน ratio เดิมไม่เคยจับเคสนี้ได้ — ถ้าเทสต์นี้แดงแปลว่าสมมติฐาน"
        "ของงานนี้เปลี่ยนไป ต้องอ่านใหม่ก่อนแก้")
    monkeypatch.setattr(config, "PDFTEXT_BAD_GLYPH_CHECK", True)
    assert ocr.text_looks_garbled(REAL_BROKEN_ZONE) is True


@pytest.mark.parametrize("text,why", [
    ("PEGMJMGOÀ\x04XYċ¤O\x04Z\x04ŁIlé. SLOŽENÍ", "อักขระควบคุม C0"),
    ("KRMIVO PR3\x8c(374Ý0 KO", "อักขระควบคุม C1"),
    ("KOVÉ KRMIVO PR DOSPLÉ", "Private Use Area"),
    ("DOPL�KOVÉ KRMIVO PR� DOSP�LÉ", "U+FFFD"),
])
def test_impossible_characters_are_flagged(text, why):
    assert ocr.text_looks_garbled(text) is True, why


@pytest.mark.parametrize("text,why", [
    # ⚠️ เคสที่อันตรายที่สุดสำหรับด่านนี้: อักขระจัดทิศทางข้อความ (category
    # "Cf") เป็นของ *ปกติ* บนฉลากอาหรับ/ฮีบรู ถ้าเผลอรวมเข้าไปจะฟ้องผิดทุกใบ
    ("‏مكونات: تونة المحيط الهادئ ٤٧٫٨٪ ‎ أرز ‍ ١٫٠٪ مرق",
     "อาหรับ + RLM/LRM/ZWJ"),
    ("‎מרכיבים: טונה אוקיינוס שקט 47.8% אורז 1.0% מרק 50.2%",
     "ฮีบรู + LRM"),
    ("COMPOSITION : thon du Pacifique (47,8 %), riz (1,0 %), "
     "chlo­rure de potassium", "NBSP + soft hyphen"),
    ("成分：太平洋鲣鱼（４７．８％）、米（１．０％）、氯化钾", "จีน + full-width"),
    ("ส่วนประกอบ: ปลาทูน่า ๔๗.๘% ข้าว ๑.๐% น้ำซุป ๕๐.๒%", "ไทย"),
    ("♻ PAP 20 · ⌛ 24 ⇒ ✓ NET WT 170 g ± 2 % ™ ® © °C", "สัญลักษณ์บนฉลาก"),
])
def test_legitimate_scripts_are_never_flagged(text, why):
    assert ocr.bad_glyph_count(text) == 0, why
    assert ocr.text_looks_garbled(text) is False, why


def test_tabs_and_newlines_are_not_evidence():
    """ช่องว่างทุกชนิดเป็นของปกติในข้อความที่สกัดมา ไม่ใช่ร่องรอยความเสียหาย."""
    assert ocr.bad_glyph_count("A\tB\nC\r\nD\x0cE\x0bF") == 0


def test_hard_signal_needs_no_minimum_word_count():
    """ต่างจากด่าน ratio: ข้อความสั้นที่มีอักขระต้องห้ามก็ตัดสินได้ทันที
    (ratio ต้องมีคำยาว >= 8 คำถึงจะยอมตัดสิน)."""
    assert ocr.text_looks_garbled("KO\x04KY") is True


def test_bad_glyph_check_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(config, "PDFTEXT_BAD_GLYPH_CHECK", False)
    assert ocr.text_looks_garbled("PEGMJMGOÀ\x04XYċ¤O") is False


def test_bad_glyph_min_count_is_configurable(monkeypatch):
    monkeypatch.setattr(config, "PDFTEXT_BAD_GLYPH_MIN_COUNT", 3)
    assert ocr.text_looks_garbled("KRMIVO PR3\x04 KO") is False   # เจอ 1 ตัว
    assert ocr.text_looks_garbled("KR\x04MIVO PR3\x04 K\x04O") is True


def test_reason_names_the_gate_that_fired():
    """สองด่านนี้เกิดจากคนละอาการ ผู้ใช้ต้องไล่ต่อคนละทาง ⇒ ข้อความต้องต่างกัน."""
    glyph = ocr.garbled_reason(REAL_BROKEN_ZONE)
    ratio = ocr.garbled_reason(GARBLED_TEXTS[0])
    assert "อักขระที่เป็นไปไม่ได้" in glyph and "U+0004" in glyph
    assert "คำผิดรูป" in ratio
    assert ocr.garbled_reason(REAL_CLEAN_ZONE) == ""
    assert ocr.garbled_reason(None) == "" and ocr.garbled_reason("") == ""


def test_control_char_text_layer_falls_back_to_ocr(ocr_on, monkeypatch):
    """เส้นทางจริง: โซนที่ text layer มีอักขระต้องห้ามต้องไม่ถูกส่งต่อด้วย
    conf 1.0 แต่ต้องไปอ่านจากภาพแทน."""
    monkeypatch.setattr(config, "PDFTEXT_GARBLED_CHECK", True)
    doc = FakeDoc(embedded=REAL_BROKEN_ZONE, first=(1400, 900))
    r = ocr.read_zone(doc, ZONE)
    assert r["engine"] == "mock" and r["text"] == "FROM OCR"
    assert r.get("conf") != 1.0
    assert "U+0004" in r.get("note", ""), "ต้องบอกหลักฐานไว้ในผล"


def test_clean_zone_of_the_same_artwork_still_uses_text_layer(ocr_on,
                                                              monkeypatch):
    """ไฟล์พี่น้องกันที่ฟอนต์ดี ต้องไม่เสียความแม่นของ text layer ไปด้วย."""
    monkeypatch.setattr(config, "PDFTEXT_GARBLED_CHECK", True)
    doc = FakeDoc(embedded=REAL_CLEAN_ZONE, first=(1400, 900))
    r = ocr.read_zone(doc, ZONE)
    assert r["engine"] == "pdf-text" and r["conf"] == 1.0
    assert doc.calls == []


# ── กันพัง: โซนเดียวพังต้องไม่ล้มการตรวจทั้งใบ ───────────────────────

def test_garbled_check_accepts_none():
    """``text_looks_garbled(None)`` เคยโยน TypeError."""
    from artwork_check import ocr as aocr
    assert aocr.text_looks_garbled(None) is False
    assert aocr.text_looks_garbled("") is False


def test_backend_exception_becomes_unreadable_zone(monkeypatch):
    """backend โยน exception → โซนนั้นเป็น UNREADABLE ไม่ใช่การตรวจล่มทั้งใบ
    (read_zone ไม่เคยมี try/except ครอบ ⇒ เดิมได้ HTTP 500 แทนรายงาน)."""
    import numpy as np
    from artwork_check import ocr as aocr

    class Doc:
        is_pdf = False
        def embedded_text(self, bbox): return ""
        def render_zone(self, bbox, dpi=None, max_side=None):
            return np.zeros((80, 240, 3), np.uint8)

    monkeypatch.setattr(aocr.vertex_client, "is_enabled", lambda: True)

    def boom(*a, **k):
        raise RuntimeError("backend ระเบิด")
    monkeypatch.setattr(aocr.vertex_client, "ocr_image", boom)

    out = aocr.read_zone(Doc(), {"id": "z1", "type": "panel",
                                 "bbox": [0.1, 0.1, 0.3, 0.2]})
    assert out["zone_id"] == "z1"
    assert out["error"] and "backend ระเบิด" in out["error"]
    assert out["text"] == ""


def test_backend_returning_non_dict_is_handled(monkeypatch):
    import numpy as np
    from artwork_check import ocr as aocr

    class Doc:
        is_pdf = False
        def embedded_text(self, bbox): return ""
        def render_zone(self, bbox, dpi=None, max_side=None):
            return np.zeros((80, 240, 3), np.uint8)

    monkeypatch.setattr(aocr.vertex_client, "is_enabled", lambda: True)
    monkeypatch.setattr(aocr.vertex_client, "ocr_image", lambda *a, **k: "ขยะ")
    out = aocr.read_zone(Doc(), {"id": "z1", "type": "panel",
                                 "bbox": [0.1, 0.1, 0.3, 0.2]})
    assert out["error"] and out["text"] == ""
