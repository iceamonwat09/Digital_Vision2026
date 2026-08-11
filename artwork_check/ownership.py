"""
ใครเห็น / เปิด / ลบ "การตรวจ artwork" รายการไหนได้บ้าง.

แยกเป็นโมดูลนโยบายล้วน ๆ — **ไม่ import Flask** — เพื่อให้เทสต์ได้ตรง ๆ และ
ให้กฎอยู่ที่เดียว (routes.py เป็นคนหา viewer จาก request แล้วส่งเข้ามา).

รูปแบบ ``viewer`` = claims ของ auth (``auth.tokens.user_claims``):
    {"sub": "7", "username": "somchai", "role": "Admin", "perms": [...]}

ค่าพิเศษ:
* ``viewer is None``  = ระบบล็อกอินปิดอยู่ (``AUTH_ENABLED=False``) หรือโหมด
  artwork ถูกใช้โดยไม่มี auth ติดตั้ง → **ไม่กรองอะไรเลย** เพราะไม่มีตัวตน
  ผู้ใช้ให้เทียบ; ถ้ากรองจะทำให้หน้าประวัติว่างเปล่าทั้งที่ระบบทำงานปกติ.
* ``viewer == {}``    = ล็อกอินเปิดอยู่แต่หาผู้ใช้ไม่ได้ (ปกติเข้าไม่ถึง
  จุดนี้เพราะ guard ของ auth ตอบ 401 ไปก่อน) → ถือว่าไม่มีสิทธิ์อะไรเลย.

``owner`` = เนื้อหาใน ``owner.json`` ของการตรวจนั้น หรือ ``None`` ถ้าเป็น
บันทึกที่สร้างก่อนมีฟีเจอร์นี้ (ไม่มีทางรู้ย้อนหลังว่าใครเป็นคนตรวจ).
"""

from __future__ import annotations

from typing import Optional

from . import config


def is_admin(viewer: Optional[dict]) -> bool:
    """viewer อยู่ใน role ที่เห็นประวัติของทุกคนหรือไม่."""
    if not viewer:
        return False
    return (viewer.get("role") or "") in config.HISTORY_ADMIN_ROLES


def can_access(owner: Optional[dict], viewer: Optional[dict]) -> bool:
    """viewer เปิด/ลบการตรวจที่เป็นของ owner ได้ไหม.

    ลำดับการตัดสิน (บนลงล่าง ข้อแรกที่ตรงชนะ):
      1. ปิดฟีเจอร์ (``HISTORY_PER_USER=False``) → ได้ทุกอย่าง (พฤติกรรมเดิม)
      2. ไม่มีระบบล็อกอิน (viewer is None)      → ได้ทุกอย่าง (พฤติกรรมเดิม)
      3. role admin                              → ได้ทุกอย่าง
      4. บันทึกเก่าที่ไม่มีเจ้าของ                → **ไม่ได้** (admin เท่านั้น)
      5. เป็นเจ้าของเอง                          → ได้
    """
    if not config.HISTORY_PER_USER:
        return True
    if viewer is None:
        return True
    if is_admin(viewer):
        return True
    if not owner:
        return False
    oid = str(owner.get("user_id") or "").strip()
    vid = str(viewer.get("sub") or "").strip()
    # ต้องมีค่าจริงทั้งคู่ — ไม่งั้น "" == "" จะกลายเป็นว่าทุกคนเป็นเจ้าของ
    # ของบันทึกที่ user_id ว่าง
    return bool(oid) and bool(vid) and oid == vid


def scope_of(viewer: Optional[dict]) -> str:
    """ป้ายบอกว่าหน้าประวัติกำลังแสดงอะไร — ใช้กับ UI เท่านั้น.

    ``"all"`` = เห็นทุกบันทึก · ``"own"`` = เห็นเฉพาะของตัวเอง
    """
    if not config.HISTORY_PER_USER or viewer is None or is_admin(viewer):
        return "all"
    return "own"


def make_filter(viewer: Optional[dict]):
    """คืน callable ``(owner) -> bool`` สำหรับส่งให้ ``report.list_inspections``.

    คืน ``None`` เมื่อไม่ต้องกรองเลย เพื่อให้ผู้เรียกใช้เส้นทางเดิมได้ตรง ๆ
    (ประหยัดการอ่าน owner.json ทีละไฟล์ และรักษาพฤติกรรมเดิมแบบเป๊ะ ๆ)
    """
    if scope_of(viewer) == "all":
        return None
    return lambda owner: can_access(owner, viewer)
