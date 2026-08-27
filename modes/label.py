"""
Configuration for Label Inspection mode.

The user trains the YOLO model externally (Roboflow + Colab) and drops the
resulting ``.pt`` file into ``weights/label/``. Class names are read from
``model.names`` at load time, so ``CLASS_NAMES`` below can stay empty until
the user pins the wording for the UI.
"""

import os

MODE_NAME = "label"
DISPLAY_NAME = "Label Inspection"

WEIGHTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "weights", "label"
)
DEFAULT_MODEL_FILE = None  # auto-pick first .pt found

# ── คลาสที่ไม่ใช่ตำหนิ — ค่าเดิมของระบบ (app.py เคย hard-code ชุดนี้ไว้) ────
# คงไว้เท่าเดิมเป๊ะเพื่อไม่ให้โหมดนี้เปลี่ยนพฤติกรรมแม้แต่นิดเดียว
NON_DEFECT_CLASSES = {"good", "can"}

# ไม่แสดงป้าย NG/OK และไม่ซ่อนกรอบใด ๆ — โหมดนี้ยังไม่ได้นิยาม "ชิ้นงานทั้งใบ"
# ⇒ พฤติกรรมการแสดงผลเท่าเดิมทุกประการ
VERDICT_BADGE = False

# Empty dict → ใช้ชื่อคลาสของโมเดลเองเป็นป้าย และ **ไม่เตือน** เรื่องคลาสที่
# ระบบไม่รู้จัก (โหมดนี้ตั้งใจรับทุกคลาสที่ผู้ใช้เทรนมา).
# Populate after the user finalises class wording.
CLASS_NAMES: dict = {}
COLORS: dict = {}
