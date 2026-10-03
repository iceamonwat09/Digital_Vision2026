"""ค่าตั้งร่วมของเทสต์

Artwork V2 รอบ 5 (3 ต.ค.) เปลี่ยนค่าเริ่มต้น 2 ตัว: ปิดการอ่านซ้ำ (``REREAD_ENABLED``)
และเปิด AI ตรวจทาน (``AI_MODE = "assist"``) — เทสต์ของ V2 ที่เขียนก่อนหน้านั้นทดสอบ
ชั้นเทียบ/การอ่านซ้ำภายใต้ค่าเดิม จึงตรึงค่าเดิมไว้ให้ (ไม่งั้นทุกเทสต์ที่รัน pipeline
จะพยายามต่อ N8N จริงบน 127.0.0.1) · ค่าเริ่มต้นใหม่ทดสอบใน ``test_artwork_v2_ai_review.py``
"""

import pytest

_NEW_DEFAULTS_TESTED_IN = {"test_artwork_v2_ai_review"}


@pytest.fixture(autouse=True)
def _artwork_v2_legacy_defaults(request, monkeypatch):
    name = request.module.__name__.rsplit(".", 1)[-1]
    if not name.startswith("test_artwork_v2") or name in _NEW_DEFAULTS_TESTED_IN:
        yield
        return
    from artwork_v2 import config
    monkeypatch.setattr(config, "REREAD_ENABLED", True)
    monkeypatch.setattr(config, "AI_MODE", "off")
    yield
