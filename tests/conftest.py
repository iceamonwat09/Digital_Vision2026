"""ค่าตั้งร่วมของเทสต์

Artwork V2 รอบ 5 (3 ต.ค.) เปลี่ยนค่าเริ่มต้น 2 ตัว: ปิดการอ่านซ้ำ (``REREAD_ENABLED``)
และเปิด AI ตรวจทาน (``AI_MODE = "assist"``) — เทสต์ของ V2 ที่เขียนก่อนหน้านั้นทดสอบ
ชั้นเทียบ/การอ่านซ้ำภายใต้ค่าเดิม จึงตรึงค่าเดิมไว้ให้ (ไม่งั้นทุกเทสต์ที่รัน pipeline
จะพยายามต่อ N8N จริงบน 127.0.0.1) · ค่าเริ่มต้นใหม่ทดสอบใน ``test_artwork_v2_ai_review.py``
"""

import os

import pytest

_NEW_DEFAULTS_TESTED_IN = {"test_artwork_v2_ai_review"}
# 7 ต.ค. (ข้อสรุปทีม): กติกาโครงสร้าง + หลักฐานภาพ เปิดเป็นค่าเริ่มต้น — เทสต์รุ่นก่อนใช้ OCR ปลอม
# บน PDF ที่หมึกเหมือนกัน (หลักฐานภาพจะพับจุดต่างปลอมนั้นถูกต้องตามหน้าที่) และล็อกผลของตัวเทียบรุ่นเดิม
# ⇒ ตรึงค่าเดิมให้ · ค่าใหม่ทดสอบในโมดูลด้านล่าง (+ ``ARTWORK_V2_TEST_NEW_RULES=1`` = รันเทสต์รุ่นก่อนด้วยค่าใหม่)
# (7 ต.ค. รอบ 5: + แยกบรรทัดที่รวมข้ามคอลัมน์ · ภาพสแกน · พื้นที่ยกเว้น — ทดสอบในโมดูลของตัวเอง)
_STRUCT_TESTED_IN = {"test_artwork_v2_structure", "test_artwork_v2_pixverify", "test_artwork_v2_split",
                     "test_artwork_v2_raster", "test_artwork_v2_ignore"}
_STRUCT_FLAGS = ("GEO_PAIRING", "RECOMPOSE", "MOVED_TEXT", "RELOCATE", "BALANCED_MOVE",
                 "VERTICAL_UPRIGHT", "QUOTE_PUNCT", "PIXEL_VERIFY", "SPLIT_MERGED")
_GUARD_TESTED_IN = {"test_artwork_v2_zone_guard"}


@pytest.fixture(autouse=True)
def _artwork_v2_legacy_defaults(request, monkeypatch):
    name = request.module.__name__.rsplit(".", 1)[-1]
    if not name.startswith("test_artwork_v2") or name in _NEW_DEFAULTS_TESTED_IN:
        yield
        return
    from artwork_v2 import config
    monkeypatch.setattr(config, "REREAD_ENABLED", True)
    monkeypatch.setattr(config, "AI_MODE", "off")
    if name not in _STRUCT_TESTED_IN and os.getenv("ARTWORK_V2_TEST_NEW_RULES") != "1":
        for k in _STRUCT_FLAGS:
            monkeypatch.setattr(config, k, False)
    # 4 ต.ค.: 1 คู่ = 1 คำขอ + ด่านกันยิงรัว — เทสต์รุ่นก่อนยิง /run ติดกันบนงานเดียว
    # และล็อกการรวมคู่ลงคำขอเดียว ⇒ ตรึงค่าเดิม (ค่าใหม่ทดสอบใน test_artwork_v2_zone_guard.py)
    if name not in _GUARD_TESTED_IN:
        monkeypatch.setattr(config, "ONE_REQUEST_PER_PAIR", False)
        monkeypatch.setattr(config, "RUN_COOLDOWN_S", 0.0)
        monkeypatch.setattr(config, "KEY_TEST_COOLDOWN_S", 0.0)
    yield
