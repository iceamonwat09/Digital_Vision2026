# -*- coding: utf-8 -*-
"""ยืนยัน defect ด้วยการอ่านซ้ำ — "อ่านสองครั้ง เชื่อเฉพาะที่ซ้ำ".

ที่มา (วัดจากข้อมูลจริงของสถานี 4 ก.ย. 2026 — 4 รอบ x 2 โซนของแผงโภชนาการ
John West ที่ต่างกันจริงข้อเดียวคือ Sodium 20% -> 24%):

    รอบ  โหมดการอ่าน        defect ที่ระบบฟ้อง
     1   เสีย + ดี              7   (จริง 1 · ปลอม 6)
     2   เสีย + เสีย            1   (จริงล้วน)
     3   ดี   + ดี              1   (จริงล้วน)
     4   ดี   + เสีย            6   (จริง 1 · ปลอม 5)

**ข้อสังเกตที่เป็นหัวใจ:** โหมด "เสีย" ของ Gemini ให้ผลเพี้ยนแบบเดิมเป๊ะทุกครั้ง
(٧ -> V · ١ -> ا · ٠ -> หาย · ٤٧٥ ملجم -> ملجم٤٧٥) ⇒ ถ้า **ทั้งสองฝั่งอยู่โหมด
เดียวกัน** ความเพี้ยนหักล้างกันหมดและการเทียบถูกต้อง — แม้จะอยู่ในโหมดเสีย
(ดูรอบ 2). defect ปลอมเกิดจาก "สองฝั่งเสียไม่พร้อมกัน" ล้วน ๆ

⇒ ความต่าง **จริง** อยู่บนแผ่นจริง จึงโผล่ทุกรอบ (24% = 4/4)
⇒ ความเพี้ยนของ OCR เป็นการสุ่ม จึงไม่ซ้ำที่โซนเดิม (สูงสุด 2/4 และสลับข้าง)

วัดทุกคู่ของ 4 รอบ (6 คู่): ตัดกันแล้ว **เหลือ `24%` ตัวเดียวทั้ง 6 คู่**

⚠️ **ต้องใช้คีย์ ``(โซน, ข้อความ)`` ไม่ใช่ข้อความเดี่ยว ๆ** — รอบ 1 กับรอบ 4
ให้ชุดข้อความ defect เหมือนกันเป๊ะแต่ **คนละโซน** (เพราะสลับกันว่าฝั่งไหนเสีย)
ถ้าดูแค่ข้อความจะเหลือ 6 รายการ ถ้าดูโซนด้วยจะเหลือ 1

⚠️ **UNREADABLE ใช้ union ไม่ใช่ intersection** — "อ่านไม่ได้" คือคำเตือนว่า
ระบบตรวจไม่ได้ ถ้าเอาไปตัดกันจะกลายเป็นการ **ซ่อนความไม่รู้** ซึ่งตรงข้ามกับ
กฎเหล็กข้อ 2 พอดี. รอบไหนอ่านไม่ได้ = รายงาน

โมดูลนี้ไม่ import Flask/OpenCV และไม่เรียก OCR เอง — รับ defect ที่คำนวณ
เสร็จแล้วมาสองชุด จึงเทสต์ได้ตรง ๆ ด้วยข้อมูลจริงที่บันทึกไว้
"""
from typing import Dict, List, Tuple
import re

# คลาสที่ "ไม่รู้" — ต้องรายงานถ้าโผล่รอบใดรอบหนึ่ง (union)
ALWAYS_REPORT = frozenset({"UNREADABLE"})

# ตัดเครื่องหมาย/ช่องว่างท้ายข้อความก่อนเทียบ — Gemini เติม `|` `.` ต่อท้าย
# ได้เป็นครั้งคราว (วัดจริง: `24%` รอบหนึ่ง กลายเป็น `24% |` อีกรอบ) ซึ่ง
# ถ้าไม่ตัดจะทำให้ **ความต่างจริงถูกทิ้ง** = false negative ที่อันตรากว่า
_TRAIL = re.compile(r"[\s|.,;:،؛]+$")


def defect_key(d: dict) -> Tuple[str, str, str]:
    """ลายเซ็นของ defect หนึ่งรายการสำหรับเทียบข้ามรอบ."""
    found = _TRAIL.sub("", str(d.get("found", "") or "").strip())
    return (str(d.get("class", "")), str(d.get("zone_id", "")), found)


def confirm(rounds: List[List[dict]]) -> Tuple[List[dict], List[dict]]:
    """รับ defect หลายรอบ → ``(ยืนยันแล้ว, ยังไม่ยืนยัน)``.

    * คลาสใน :data:`ALWAYS_REPORT` → union (โผล่รอบใดก็รายงาน)
    * คลาสอื่น → intersection (ต้องโผล่ **ทุกรอบ**)

    ลำดับและตัวออบเจ็กต์ของ defect ยึดจาก **รอบแรก** เสมอ เพื่อให้ผลที่
    แสดงกับผู้ใช้เหมือนเดิมทุกประการ (เป็นการ *กรอง* ไม่ใช่การสร้างใหม่).
    ``ยังไม่ยืนยัน`` คือรายการที่ตกไป — ต้องเอาไปแสดงเป็นคำแนะนำ ไม่ใช่
    ทิ้งเงียบ ๆ
    """
    rounds = [r for r in rounds if r is not None]
    if len(rounds) < 2:
        return (list(rounds[0]) if rounds else []), []

    keysets = [set(map(defect_key, r)) for r in rounds]
    common = set.intersection(*keysets)

    confirmed: List[dict] = []
    unconfirmed: List[dict] = []
    seen = set()
    for i, r in enumerate(rounds):
        for d in r:
            k = defect_key(d)
            if k in seen:
                continue
            always = str(d.get("class", "")) in ALWAYS_REPORT
            if always or k in common:
                # รอบแรกเป็นเจ้าของผล — รอบหลังเติมได้เฉพาะคลาส union
                if i == 0 or always:
                    seen.add(k)
                    confirmed.append(d)
            elif i == 0:
                seen.add(k)
                unconfirmed.append(d)
    # รายการที่โผล่เฉพาะรอบหลัง ๆ ก็ถือว่า "ยังไม่ยืนยัน" เช่นกัน
    for i, r in enumerate(rounds[1:], 1):
        for d in r:
            k = defect_key(d)
            if k not in seen and k not in common:
                seen.add(k)
                unconfirmed.append(d)
    return confirmed, unconfirmed


def summary(confirmed: List[dict], unconfirmed: List[dict],
            n_rounds: int) -> Dict[str, object]:
    """ข้อมูลสำหรับแสดงบนรายงาน (advisory ล้วน)."""
    return {
        "rounds": int(n_rounds),
        "confirmed": len(confirmed),
        "unconfirmed": len(unconfirmed),
        "items": [
            {"class": d.get("class", ""), "zone_id": d.get("zone_id", ""),
             "found": d.get("found", ""), "message": d.get("message", "")}
            for d in unconfirmed
        ],
    }
