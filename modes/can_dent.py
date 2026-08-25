"""
Configuration for Can Dent Detection mode.
Class names / colors / Thai display labels for the can-body inspection model.
"""

import os

MODE_NAME = "can_dent"
DISPLAY_NAME = "Can Dent Detection"

WEIGHTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "weights", "can_dent"
)
# ไฟล์ที่จะใช้เมื่อผู้ใช้ยังไม่ได้เลือกเอง — ถ้าไม่มีไฟล์นี้ registry จะเลือก
# ``.pt`` ตัวแรกในโฟลเดอร์ให้ (และ log ว่าเลือกตัวไหน)
# ⚠️ ทุกไฟล์ ``.pt`` ในโหมดนี้ใช้ตรรกะเดียวกันหมด (บทบาทของคลาส) ⇒ ชื่อไฟล์
# ไม่มีผลต่อการทำงานอีกต่อไป มีผลแค่ "จะหยิบตัวไหนมาใช้เป็นค่าเริ่มต้น"
DEFAULT_MODEL_FILE = "bestX.pt"

# YOLO class id → raw name (kept for backward compatibility / fallback only).
# At runtime ``yolo_detector`` reads ``model.names`` directly.
CLASS_IDS = {
    0: "dented",
    1: "dented_spot",
    2: "good",
}

# ── คลาสที่ **ไม่ใช่ตำหนิ** (กล่อง "กระป๋องทั้งใบ") ──────────────────────────
# คลาสอื่นทุกตัวที่โมเดลมี = ตำหนิ ⇒ นับ NG + บันทึก DB
# ⚠️ โมเดลใหม่ที่ตั้งชื่อคลาสทั้งใบเป็นอย่างอื่น (เช่น "body"/"ok") **ต้องมา
# เพิ่มชื่อตรงนี้** ไม่งั้นจะถูกนับเป็นตำหนิ = NG ปลอมทุกใบ
# (ระบบจะขึ้นคำเตือนบนหน้าเว็บให้เองเมื่อเจอคลาสที่ไม่รู้จัก)
NON_DEFECT_CLASSES = {"good", "can"}

# แสดงป้าย NG/OK มุมขวาบน + ซ่อนกรอบ "ทั้งใบ" เมื่อผลเป็น NG
# (ตรรกะเดิมของ bestX.pt — ตอนนี้ใช้กับ **ทุกไฟล์ .pt** ในโหมดนี้)
VERDICT_BADGE = True

# Display names — ป้ายชื่อเท่านั้น ไม่ใช่ตัวกรอง: คลาสที่ไม่มีในนี้ยังถูกตรวจ
# และแสดงตามปกติ (ใช้ชื่อของตัวมันเอง) พร้อมคำเตือนว่าระบบไม่รู้จัก
CLASS_NAMES = {
    "dent":        "Can Dent",     # bestX.pt (segmentation, 2 คลาส)
    "can":         "Can Good",
    "dented":      "Can Dent",     # best.pt (detection, 3 คลาส — รุ่นเก่า)
    "dented_spot": "Dent Area",
    "good":        "Can Good",
}

# BGR colors for drawing — ไม่ได้กำหนดไว้ = ใช้สีตามบทบาท
# (ตำหนิ = แดง · ทั้งใบ = เขียว)
COLORS = {
    "good":        (80, 200,   0),   # green
    "can":         (80, 200,   0),   # green
    "dented":      (0,   0, 220),    # red
    "dent":        (0,   0, 220),    # red
    "dented_spot": (0, 100, 255),    # orange
}
