"""
cache ของแท็บ "ข้อความ + คำแปล" (``ocr_only.json``) ต้องหลุดเมื่อค่าตั้งที่
เปลี่ยนผลการอ่านข้อความเปลี่ยนไป — ไม่ใช่แค่ตอนโซนขยับ.

ทำไมสำคัญ: การเปิด ``OCR_CROP_MIN_SIDE`` ทำให้ไฟล์ที่ถูกย่อลง A4 อ่านได้จาก
1.2% เป็น 97.6% แต่ถ้า cache key ไม่รวมค่านี้ งานที่เคยกดแปลไปแล้วจะยังได้
ข้อความชุดเก่าตลอดไป (layout โซนไม่ได้เปลี่ยน) — ผิดแบบเงียบและหาสาเหตุยาก.
"""

import pytest

from artwork_check import config, pipeline


ZONES = [
    {"id": "z1", "type": "panel", "group": "A", "bbox": [0.1, 0.1, 0.3, 0.2],
     "doc": "a", "rotate": 0},
    {"id": "z2", "type": "panel", "group": "A", "bbox": [0.5, 0.1, 0.3, 0.2],
     "doc": "a", "rotate": 0},
]


def sig(auto=False):
    return pipeline._zones_signature(ZONES, auto)


def test_same_input_same_signature():
    assert sig() == sig()


def test_zone_move_changes_signature():
    moved = [dict(ZONES[0], bbox=[0.11, 0.1, 0.3, 0.2]), ZONES[1]]
    assert pipeline._zones_signature(moved) != sig()


def test_auto_rotate_changes_signature():
    assert sig(auto=True) != sig(auto=False)


# ── ค่าตั้งที่เปลี่ยนผลการอ่าน ต้องทำให้ cache หลุดทุกตัว ────────────

@pytest.mark.parametrize("attr,new_value", [
    ("OCR_DPI", 900),
    ("OCR_CROP_MAX_SIDE", 2000),
    ("OCR_CROP_MIN_SIDE", 0),
    ("OCR_DPI_MAX_FACTOR", 2.0),
    ("EMBEDDED_TEXT_MIN_CHARS", 40),
    ("PDFTEXT_GARBLED_CHECK", False),
    ("PDFTEXT_GARBLED_MIN_TOKENS", 20),
    ("PDFTEXT_GARBLED_RATIO", 0.6),
    # ด่านอักขระต้องห้าม + การอ่านซ้ำให้ engine ตรงกันทั้งกลุ่ม — ทั้งคู่
    # เปลี่ยนแล้ว "ข้อความที่โซนหนึ่งได้" เปลี่ยนได้จริง (ใช้ text layer vs OCR)
    ("PDFTEXT_BAD_GLYPH_CHECK", False),
    ("PDFTEXT_BAD_GLYPH_MIN_COUNT", 5),
    ("OCR_GROUP_ENGINE_CONSISTENCY", True),
    ("PDFTEXT_FONT_EVIDENCE", "off"),
])
def test_ocr_setting_change_invalidates_cache(monkeypatch, attr, new_value):
    before = sig()
    monkeypatch.setattr(config, attr, new_value)
    assert sig() != before, (
        "เปลี่ยน %s แล้ว cache ต้องหลุด ไม่งั้นแท็บแปลจะเสิร์ฟข้อความเก่า" % attr)


def test_fingerprint_covers_every_ocr_setting():
    """กันลืม: ถ้ามีใครเพิ่มค่าตั้งที่กระทบการอ่านแล้วไม่ใส่ใน fingerprint
    เทสต์ชุดบนจะไม่จับ — ตรวจรายชื่อคีย์ตรง ๆ อีกชั้น."""
    fp = pipeline._ocr_fingerprint()
    expected = {"dpi", "max_side", "min_side", "dpi_max_factor",
                "embed_min", "garbled", "garbled_tokens", "garbled_ratio",
                "bad_glyph", "bad_glyph_min", "group_engine",
                "font_evidence"}
    assert set(fp) == expected


def test_cache_roundtrip_respects_fingerprint(tmp_path, monkeypatch):
    """เขียน cache ด้วยค่าตั้งชุดหนึ่ง แล้วอ่านด้วยอีกชุด ต้องไม่ได้ของเก่า."""
    d = str(tmp_path)
    rows = [{"zone_id": "z1", "text": "OLD", "engine": "n8n"}]
    pipeline._save_ocr_cache(d, ZONES, rows)
    assert pipeline._load_ocr_cache(d, ZONES) == rows      # ค่าตั้งเดิม → hit

    monkeypatch.setattr(config, "OCR_CROP_MIN_SIDE", 0)
    assert pipeline._load_ocr_cache(d, ZONES) is None      # ค่าตั้งใหม่ → miss
